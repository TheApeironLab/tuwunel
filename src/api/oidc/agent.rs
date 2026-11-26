//! Exchange only a short-lived, site-brokered user access token. Account
//! association is the same provider/subject row established by browser SSO.
use axum::{Json, body::Body, response::IntoResponse};
use http::Response;
use serde_json::{Value, json};
use tuwunel_core::{Result, err, utils::time::now};
use tuwunel_service::{Services, client::read_response_capped, oauth::unique_id_sub};

use super::{require_account_usable, token::TokenRequest};

const ACCESS: &str = "urn:ietf:params:oauth:token-type:access_token";

fn verified_subject<'a>(
	info: &'a Value,
	issuer: &str,
	audience: &str,
	tenant: &str,
	brokers: &[String],
	now: u64,
) -> Option<(&'a str, &'a str, &'a str, u64)> {
	let aud = &info["aud"];
	let includes = |list: &Value, wanted: &str| {
		list.as_array().is_some_and(|v| {
			v.iter()
				.any(|entry| entry.as_str() == Some(wanted))
		})
	};
	let broker = info["azp"].as_str()?;
	let sub = info["sub"].as_str()?;
	let session_id = info["sid"].as_str()?;
	let expiry = info["exp"].as_u64()?;
	let issued = info["iat"].as_u64()?;
	(info["active"] == true
		&& info["typ"] == "Bearer"
		&& info["iss"] == issuer
		&& (aud.as_str() == Some(audience) || includes(aud, audience))
		&& !tenant.is_empty()
		&& info["tenant"] == tenant
		&& brokers.iter().any(|allowed| allowed == broker)
		&& includes(&info["roles"], "matrix-user")
		&& !sub.is_empty()
		&& !broker.is_empty()
		&& !session_id.is_empty()
		&& info
			.get("nbf")
			.is_none_or(|value| value.as_u64().is_some_and(|start| start <= now))
		&& issued <= now
		&& now < expiry
		&& expiry.saturating_sub(issued) <= 300)
		.then_some((sub, broker, session_id, expiry))
}

pub(super) async fn exchange(services: &Services, body: &TokenRequest) -> Result<Response<Body>> {
	let denied = || err!(Request(Forbidden("Chat agent grant rejected")));
	let token = body
		.subject_token
		.as_deref()
		.filter(|t| !t.is_empty() && t.len() <= 16384)
		.ok_or_else(denied)?;
	if body.subject_token_type.as_deref() != Some(ACCESS)
		|| body.requested_token_type.as_deref() != Some(ACCESS)
	{
		return Err(denied());
	}
	let provider = services
		.oauth
		.providers
		.find_default_config()
		.ok_or_else(denied)?;
	if provider.agent_broker_clients.is_empty() || provider.agent_tenant.is_empty() {
		return Err(denied());
	}
	let issuer = provider
		.issuer_url
		.as_ref()
		.filter(|u| u.scheme() == "https")
		.ok_or_else(denied)?
		.as_str()
		.trim_end_matches('/');
	let form =
		serde_html_form::to_string(&[("token", token), ("token_type_hint", "access_token")])
			.map_err(|_| denied())?;
	let response = services
		.client
		.oauth
		.post(format!("{issuer}/protocol/openid-connect/token/introspect"))
		.basic_auth(&provider.client_id, Some(provider.get_client_secret().await?))
		.header(http::header::CONTENT_TYPE, "application/x-www-form-urlencoded")
		.body(form)
		.timeout(std::time::Duration::from_secs(5))
		.send()
		.await
		.map_err(|_| denied())?;
	if !response.status().is_success() {
		return Err(denied());
	}
	let info: Value = serde_json::from_slice(&read_response_capped(response, 65536).await?)
		.map_err(|_| denied())?;
	let (sub, broker, session_id, expires_at) = verified_subject(
		&info,
		issuer,
		&provider.client_id,
		&provider.agent_tenant,
		&provider.agent_broker_clients,
		now().as_secs(),
	)
	.ok_or_else(denied)?;
	let identity = unique_id_sub((provider, sub))?;
	let session = services
		.oauth
		.sessions
		.get_by_unique_id(&identity)
		.await
		.map_err(|_| denied())?;
	if session.idp_id.as_deref() != Some(provider.id())
		|| session.user_info.as_ref().map(|i| i.sub.as_str()) != Some(sub)
	{
		return Err(denied());
	}
	let user = session.user_id.ok_or_else(denied)?;
	require_account_usable(services, &user).await?;
	let broker_session = tuwunel_service::oauth::unique_id_iss_sub((broker, session_id))?;
	let device = format!(
		"{}{}",
		tuwunel_service::users::device::AGENT_DEVICE_PREFIX,
		tuwunel_service::oauth::unique_id_iss_sub((&broker_session, &identity))?
	);
	let access = services
		.users
		.agent_access_token(&user, device.as_str().into(), expires_at)
		.await?;
	Ok(
		Json(json!({"access_token":access,"issued_token_type":ACCESS,"token_type":"Bearer",
		"expires_in":expires_at.saturating_sub(now().as_secs())}))
		.into_response(),
	)
}

#[cfg(test)]
mod tests {
	use super::*;
	#[test]
	fn binds_every_delegated_claim() {
		let valid = json!({"active":true,"typ":"Bearer","iss":"https://idp/realm","aud":["matrix"],
			"tenant":"site","azp":"broker","sub":"subject","sid":"login","roles":["matrix-user"],"iat":100,"exp":400});
		let check = |v: &Value| {
			verified_subject(v, "https://idp/realm", "matrix", "site", &["broker".into()], 101)
				.is_some()
		};
		assert!(check(&valid));
		for (name, value) in [
			("active", json!(false)),
			("typ", json!("ID")),
			("iss", json!("other")),
			("aud", json!("apeiron")),
			("tenant", json!("other")),
			("azp", json!("web")),
			("sub", json!("")),
			("roles", json!([])),
			("sid", json!("")),
			("nbf", json!(102)),
			("iat", json!(102)),
			("exp", json!(101)),
			("exp", json!(401)),
		] {
			let mut invalid = valid.clone();
			invalid[name] = value;
			assert!(!check(&invalid), "{name}");
		}
	}
}
