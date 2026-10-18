//! Upstream OIDC session revocation. Bind each token (not the user's latest
//! login) to its original SSO session. Durable tombstones also fence a token
//! exchange or refresh which races with a logout notification.
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::Mutex;
use tuwunel_core::{Err, Result, err, jwt, utils::time::now};
use tuwunel_database::{Deserialized, Json, Map};

use super::{Provider, Service, Session};

const LOGOUT_EVENT: &str = "http://schemas.openid.net/event/backchannel-logout";

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Binding {
	pub provider: String,
	pub issuer: String,
	pub subject: String,
	pub sid: String,
	pub authenticated_at: u64,
}

pub struct Backchannel {
	grants: Arc<Map>,
	tokens: Arc<Map>,
	revocations: Arc<Map>,
	write_lock: Mutex<()>,
	required: bool,
}

impl Backchannel {
	pub(super) fn build(args: &crate::Args<'_>) -> Self {
		Self {
			grants: args.db["ssogrant_binding"].clone(),
			tokens: args.db["ssotoken_binding"].clone(),
			revocations: args.db["ssosession_revoked"].clone(),
			write_lock: Mutex::new(()),
			required: args.server.config.sso_require_session_binding,
		}
	}

	pub fn required(&self) -> bool {
		self.required
	}

	pub fn put_grant(&self, token: &str, binding: &Binding) {
		self.grants.raw_put(token, Json(binding));
	}

	pub async fn take_grant(&self, token: &str) -> Result<Option<Binding>> {
		let binding = match self.grants.get(token).await {
			| Ok(value) => Some(value.deserialized::<Json<Binding>>()?.0),
			| Err(error) if error.is_not_found() => None,
			| Err(error) => return Err(error),
		};
		self.grants.remove(token);
		self.check_optional(binding.as_ref()).await?;
		Ok(binding)
	}

	pub async fn check_optional(&self, binding: Option<&Binding>) -> Result {
		let Some(binding) = binding else {
			return if self.required {
				Err!(Request(Forbidden("SSO reauthentication required")))
			} else {
				Ok(())
			};
		};
		for (kind, id, subject) in [
			("sid", binding.sid.as_str(), ""),
			("sid", binding.sid.as_str(), binding.subject.as_str()),
			("sub", binding.subject.as_str(), ""),
		] {
			match self
				.revocations
				.qry(&(binding.provider.as_str(), binding.issuer.as_str(), kind, id, subject))
				.await
			{
				| Ok(value) => {
					let revoked_at: u64 = value.deserialized()?;
					if kind == "sid" || binding.authenticated_at <= revoked_at {
						return Err!(Request(Forbidden("SSO session has ended")));
					}
				},
				| Err(error) if error.is_not_found() => {},
				| Err(error) => return Err(error),
			}
		}
		Ok(())
	}

	pub async fn token_binding(&self, token: &str) -> Result<Option<Binding>> {
		let binding = match self.tokens.get(token).await {
			| Ok(value) => Some(value.deserialized::<Json<Binding>>()?.0),
			| Err(error) if error.is_not_found() => None,
			| Err(error) => return Err(error),
		};
		self.check_optional(binding.as_ref()).await?;
		Ok(binding)
	}

	pub async fn bind_tokens(
		&self,
		binding: Option<&Binding>,
		access: &str,
		refresh: Option<&str>,
	) -> Result {
		self.check_optional(binding).await?;
		if let Some(binding) = binding {
			self.tokens.raw_put(access, Json(binding));
			if let Some(refresh) = refresh {
				self.tokens.raw_put(refresh, Json(binding));
			}
		}
		Ok(())
	}

	pub async fn inherit(&self, old: &str, access: &str, refresh: Option<&str>) -> Result {
		let binding = self.token_binding(old).await?;
		self.bind_tokens(binding.as_ref(), access, refresh)
			.await
	}

	pub async fn revoke(&self, provider: &Provider, claims: &Value) -> Result {
		validate_logout_claims(claims, now().as_secs())?;
		let issuer = provider
			.issuer_url
			.as_ref()
			.ok_or_else(|| err!("Missing OIDC issuer"))?
			.as_str();
		let (kind, id) = match claims.get("sid").and_then(Value::as_str) {
			| Some(sid) => ("sid", sid),
			| None => (
				"sub",
				claims["sub"]
					.as_str()
					.ok_or_else(|| err!("Missing logout subject"))?,
			),
		};
		let subject = if kind == "sid" {
			claims
				.get("sub")
				.and_then(Value::as_str)
				.unwrap_or("")
		} else {
			""
		};
		let key = (provider.id(), issuer, kind, id, subject);
		let _guard = self.write_lock.lock().await;
		let previous = match self.revocations.qry(&key).await {
			| Ok(value) => value.deserialized::<u64>()?,
			| Err(error) if error.is_not_found() => 0,
			| Err(error) => return Err(error),
		};
		let issued_at = claims["iat"]
			.as_u64()
			.ok_or_else(|| err!("Missing logout iat"))?;
		self.revocations.put(key, issued_at.max(previous));
		Ok(())
	}
}

impl Service {
	/// Fetch keys exclusively from the configured issuer's discovery document.
	/// No URL or algorithm supplied by a JWT can select a different trust root.
	pub async fn verify_upstream_jwt(
		&self,
		provider: &Provider,
		token: &str,
		logout: bool,
	) -> Result<Value> {
		if token.len() > 16384 {
			return Err!(Request(Forbidden("OIDC token too large")));
		}
		let header = jwt::decode_header(token)
			.map_err(|_| err!(Request(Forbidden("Invalid OIDC token"))))?;
		if header.alg != jwt::Algorithm::RS256 {
			return Err!(Request(Forbidden("Unsupported OIDC signing algorithm")));
		}
		let kid = header
			.kid
			.as_deref()
			.ok_or_else(|| err!(Request(Forbidden("Missing signing key ID"))))?;
		let discovery = self.providers.discover(provider).await?;
		let issuer = provider
			.issuer_url
			.as_ref()
			.ok_or_else(|| err!("Missing OIDC issuer"))?;
		if discovery["issuer"].as_str() != Some(issuer.as_str()) {
			return Err!(Request(Forbidden("OIDC issuer mismatch")));
		}
		let jwks_url: url::Url = discovery["jwks_uri"]
			.as_str()
			.ok_or_else(|| err!("Missing JWKS URI"))?
			.parse()?;
		if jwks_url.origin() != issuer.origin() {
			return Err!(Request(Forbidden("JWKS origin must match issuer")));
		}
		let keys = self
			.request((None, None), reqwest::Method::GET, jwks_url, Option::<()>::None)
			.await?;
		let keys: jwt::jwk::JwkSet = serde_json::from_value(keys)?;
		let key = keys
			.find(kid)
			.ok_or_else(|| err!(Request(Forbidden("Unknown OIDC signing key"))))?;
		let key = jwt::DecodingKey::from_jwk(key)
			.map_err(|_| err!(Request(Forbidden("Invalid OIDC signing key"))))?;
		verify_signed_claims(token, &key, issuer.as_str(), &provider.client_id, logout)
	}

	pub async fn binding_from_session(
		&self,
		provider: &Provider,
		session: &Session,
		subject: &str,
	) -> Result<Option<Binding>> {
		if !self.backchannel.required() {
			return Ok(None);
		}
		let token = session
			.id_token
			.as_deref()
			.ok_or_else(|| err!(Request(Forbidden("SSO ID token required"))))?;
		let claims = self
			.verify_upstream_jwt(provider, token, false)
			.await?;
		let binding = binding_from_claims(
			provider.id(),
			&claims,
			session.query_nonce.as_deref(),
			subject,
			now().as_secs(),
		)?;
		self.backchannel
			.check_optional(Some(&binding))
			.await?;
		Ok(Some(binding))
	}
}

fn verify_signed_claims(
	token: &str,
	key: &jwt::DecodingKey,
	issuer: &str,
	audience: &str,
	logout: bool,
) -> Result<Value> {
	let mut validation = jwt::Validation::new(jwt::Algorithm::RS256);
	validation.validate_nbf = true;
	validation.set_issuer(&[issuer]);
	validation.set_audience(&[audience]);
	validation.set_required_spec_claims(if logout {
		&["iss", "aud", "iat", "exp"][..]
	} else {
		&["iss", "aud", "iat", "exp", "sub"][..]
	});
	let claims = jwt::decode::<Value>(token, key, &validation)
		.map_err(|_| err!(Request(Forbidden("OIDC token verification failed"))))?
		.claims;
	Ok(claims)
}

fn binding_from_claims(
	provider: &str,
	claims: &Value,
	nonce: Option<&str>,
	subject: &str,
	now: u64,
) -> Result<Binding> {
	if nonce.is_none()
		|| claims["nonce"].as_str() != nonce
		|| claims["sub"].as_str() != Some(subject)
	{
		return Err!(Request(Forbidden("SSO identity or nonce mismatch")));
	}
	let string = |name: &str| {
		claims[name]
			.as_str()
			.filter(|value| !value.is_empty())
			.map(str::to_owned)
			.ok_or_else(|| err!(Request(Forbidden("Missing SSO session claim"))))
	};
	let issued = claims["iat"]
		.as_u64()
		.ok_or_else(|| err!(Request(Forbidden("Missing ID token iat"))))?;
	let authenticated = claims
		.get("auth_time")
		.map(|value| {
			value
				.as_u64()
				.ok_or_else(|| err!(Request(Forbidden("Invalid auth_time"))))
		})
		.transpose()?
		.unwrap_or(issued);
	if issued > now.saturating_add(60) || authenticated > issued {
		return Err!(Request(Forbidden("Invalid SSO authentication time")));
	}
	Ok(Binding {
		provider: provider.to_owned(),
		issuer: string("iss")?,
		subject: subject.to_owned(),
		sid: string("sid")?,
		authenticated_at: authenticated,
	})
}

fn validate_logout_claims(claims: &Value, now: u64) -> Result {
	let issued = claims["iat"]
		.as_u64()
		.ok_or_else(|| err!(Request(Forbidden("Missing logout iat"))))?;
	let present = |name: &str| {
		claims
			.get(name)
			.and_then(Value::as_str)
			.is_some_and(|value| !value.is_empty())
	};
	let event = claims
		.get("events")
		.and_then(|events| events.get(LOGOUT_EVENT));
	if issued > now.saturating_add(60)
		|| now.saturating_sub(issued) > 300
		|| !present("jti")
		|| (!present("sid") && !present("sub"))
		|| claims.get("nonce").is_some()
		|| !event.is_some_and(|value| {
			value
				.as_object()
				.is_some_and(|object| object.is_empty())
		}) || ["sid", "sub"]
		.iter()
		.any(|name| claims.get(*name).is_some() && !present(*name))
	{
		return Err!(Request(Forbidden("Invalid OIDC logout claims")));
	}
	Ok(())
}

#[cfg(test)]
mod tests {
	use super::*;
	use serde_json::json;

	#[test]
	fn signed_claims_reject_wrong_issuer_audience_signature_expiry_and_algorithm() {
		let signing = jwt::EncodingKey::from_rsa_pem(include_bytes!(
			"../../../nix/pkgs/complement/private_key.key"
		))
		.unwrap();
		let key = jwt::DecodingKey::from_rsa_pem(include_bytes!("backchannel-test-public.pem"))
			.unwrap();
		let issued = now().as_secs();
		let claims = json!({"iss":"https://iam.example.internal","aud":"matrix-app","iat":issued,"exp":issued+300});
		let sign = |claims: &Value| {
			jwt::encode(&jwt::Header::new(jwt::Algorithm::RS256), claims, &signing).unwrap()
		};
		let token = sign(&claims);
		assert!(
			verify_signed_claims(
				&token,
				&key,
				"https://iam.example.internal",
				"matrix-app",
				true
			)
			.is_ok()
		);
		assert!(
			verify_signed_claims(
				&token,
				&key,
				"https://other.example.internal",
				"matrix-app",
				true
			)
			.is_err()
		);
		assert!(
			verify_signed_claims(&token, &key, "https://iam.example.internal", "other-app", true)
				.is_err()
		);
		let mut expired = claims.clone();
		expired["exp"] = json!(issued - 120);
		assert!(
			verify_signed_claims(
				&sign(&expired),
				&key,
				"https://iam.example.internal",
				"matrix-app",
				true
			)
			.is_err()
		);
		let mut wrong = token.into_bytes();
		let last = wrong.len() - 5;
		wrong[last] = if wrong[last] == b'A' { b'B' } else { b'A' };
		assert!(
			verify_signed_claims(
				std::str::from_utf8(&wrong).unwrap(),
				&key,
				"https://iam.example.internal",
				"matrix-app",
				true
			)
			.is_err()
		);
		let hmac = jwt::encode(
			&jwt::Header::new(jwt::Algorithm::HS256),
			&claims,
			&jwt::EncodingKey::from_secret(b"test-only"),
		)
		.unwrap();
		assert!(
			verify_signed_claims(&hmac, &key, "https://iam.example.internal", "matrix-app", true)
				.is_err()
		);
	}

	#[test]
	fn logout_requires_event_session_id_freshness_and_no_nonce() {
		let valid =
			json!({"iat":1000,"jti":"event-1","sid":"session-1","events":{LOGOUT_EVENT:{}}});
		assert!(validate_logout_claims(&valid, 1000).is_ok());
		for (key, value) in [
			("nonce", Value::Null),
			("events", json!({})),
			("sid", json!("")),
			("iat", json!(0)),
			("iat", json!(2000)),
			("jti", json!("")),
		] {
			let mut claims = valid.clone();
			claims[key] = value;
			assert!(validate_logout_claims(&claims, 1000).is_err(), "{key}");
		}
		let mut subject = valid;
		subject.as_object_mut().unwrap().remove("sid");
		subject["sub"] = json!("alice");
		assert!(validate_logout_claims(&subject, 1000).is_ok());
	}

	#[test]
	fn binding_uses_exact_verified_subject_nonce_and_session() {
		let claims = json!({"iss":"https://iam.example.internal/realms/master","sub":"alice","nonce":"n","sid":"s1","iat":1000,"auth_time":900});
		let binding =
			binding_from_claims("matrix-app", &claims, Some("n"), "alice", 1000).unwrap();
		assert_eq!(binding.sid, "s1");
		assert_eq!(binding.authenticated_at, 900);
		assert!(
			binding_from_claims("matrix-app", &claims, Some("other"), "alice", 1000).is_err()
		);
		assert!(binding_from_claims("matrix-app", &claims, Some("n"), "bob", 1000).is_err());
		assert!(binding_from_claims("matrix-app", &claims, None, "alice", 1000).is_err());
	}
}
