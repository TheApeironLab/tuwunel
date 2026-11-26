use crate::http::{checked, error_code, field, origin};
use anyhow::{Context, Result, bail, ensure};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
	fs::{self, File, OpenOptions},
	io::{Read, Write},
	os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
	path::Path,
	thread::sleep,
	time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tempfile::NamedTempFile;
use url::Url;
use uuid::Uuid;

const DEVICE_GRANT: &str = "urn:ietf:params:oauth:grant-type:device_code";

pub fn private_file(path: &Path) -> Result<File> {
	ensure!(path.is_absolute(), "credential file path must be absolute");
	let file = OpenOptions::new()
		.read(true)
		.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
		.open(path)?;
	let meta = file.metadata()?;
	ensure!(
		meta.is_file()
			&& meta.permissions().mode() & 0o077 == 0
			&& meta.uid() == unsafe { libc::geteuid() },
		"credential file must be a private regular file owned by the current user"
	);
	Ok(file)
}

fn lock(path: &Path) -> Result<File> {
	ensure!(path.is_absolute(), "session path must be absolute");
	let mut name = path.as_os_str().to_owned();
	name.push(".lock");
	let file = OpenOptions::new()
		.read(true)
		.write(true)
		.create(true)
		.mode(0o600)
		.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
		.open(name)?;
	ensure!(file.metadata()?.is_file(), "expected regular session lock file");
	file.lock()?;
	Ok(file)
}

#[derive(Serialize, Deserialize)]
struct Session {
	server: Url,
	client_id: String,
	token_endpoint: Url,
	access_token: String,
	refresh_token: String,
	expires_at: Option<u64>,
}

fn save(path: &Path, session: &Session) -> Result<()> {
	let mut file = NamedTempFile::new_in(
		path.parent()
			.context("session needs a parent directory")?,
	)?;
	serde_json::to_writer(&mut file, session)?;
	file.flush()?;
	file.as_file().sync_all()?;
	file.persist(path)?;
	Ok(())
}

fn now() -> Result<u64> {
	Ok(SystemTime::now()
		.duration_since(UNIX_EPOCH)?
		.as_secs())
}

fn expires(value: &Value) -> Result<Option<u64>> {
	value["expires_in"]
		.as_u64()
		.map(|secs| now().map(|n| n.saturating_add(secs)))
		.transpose()
}

fn same_origin(server: &Url, raw: &str) -> Result<Url> {
	let url = Url::parse(raw)?;
	ensure!(
		server.origin() == url.origin() && url.username().is_empty() && url.password().is_none(),
		"unexpected authorization endpoint origin"
	);
	Ok(url)
}

pub struct Auth<'a> {
	pub client: Client,
	pub server: Url,
	session: Option<&'a Path>,
	token_file: Option<&'a Path>,
}

impl<'a> Auth<'a> {
	pub fn load(
		client: Client,
		server: Option<Url>,
		session: Option<&'a Path>,
		token_file: Option<&'a Path>,
	) -> Result<Self> {
		let server = if let Some(file) = session {
			let saved: Session = serde_json::from_reader(private_file(file)?)?;
			ensure!(
				server
					.as_ref()
					.is_none_or(|url| url == &saved.server),
				"session belongs to another server"
			);
			saved.server
		} else {
			ensure!(token_file.is_some(), "supply --session or --token-file");
			server.context("--server is required with --token-file")?
		};
		origin(&server)?;
		Ok(Self { client, server, session, token_file })
	}

	pub fn token(&self) -> Result<String> {
		if let Some(path) = self.token_file {
			let mut buf = String::new();
			private_file(path)?
				.take(65537)
				.read_to_string(&mut buf)?;
			let token = buf.trim();
			ensure!(
				!token.is_empty() && buf.len() <= 65536 && !token.contains(char::is_whitespace),
				"invalid token file"
			);
			return Ok(token.to_owned());
		}
		let path = self.session.context("missing session")?;
		let _guard = lock(path)?;
		let mut session: Session = serde_json::from_reader(private_file(path)?)?;
		ensure!(session.server == self.server, "session belongs to another server");
		let renew_before = now()?.saturating_add(30);
		if session
			.expires_at
			.is_some_and(|time| time <= renew_before)
		{
			let url = same_origin(&self.server, session.token_endpoint.as_str())?;
			let value: Value = checked(
				self.client
					.post(url)
					.form(&[
						("grant_type", "refresh_token"),
						("client_id", &session.client_id),
						("refresh_token", &session.refresh_token),
					])
					.send()?,
			)?
			.json()?;
			session.access_token = field(&value, "access_token")?.to_owned();
			if let Some(token) = value["refresh_token"].as_str() {
				session.refresh_token = token.to_owned();
			}
			session.expires_at = expires(&value)?;
			save(path, &session)?;
		}
		Ok(session.access_token)
	}
}

pub fn login(
	client: &Client,
	server: &Url,
	path: &Path,
	chat: Option<Url>,
	client_id: Option<String>,
) -> Result<Value> {
	origin(server)?;
	let _guard = lock(path)?;
	if path.try_exists()? {
		bail!("session exists; choose a fresh session path or log out first");
	}
	let metadata: Value = checked(
		client
			.get(server.join("/_matrix/client/v1/auth_metadata")?)
			.send()?,
	)?
	.json()?;
	let oauth_url = |key| same_origin(server, field(&metadata, key)?);
	let token_endpoint = oauth_url("token_endpoint")?;
	let id = if let Some(id) = client_id {
		id
	} else {
		let chat = chat.context("supply the Element --chat-url or a registered --client-id")?;
		origin(&chat)?;
		ensure!(chat.scheme() == "https", "--chat-url must be HTTPS");
		let registered: Value = checked(
			client
				.post(oauth_url("registration_endpoint")?)
				.json(&json!({
					"client_name":env!("CARGO_PKG_NAME"), "client_uri":chat, "redirect_uris":[chat],
					"application_type":"native", "token_endpoint_auth_method":"none",
					"grant_types":["authorization_code","refresh_token",DEVICE_GRANT], "response_types":["code"]
				}))
				.send()?,
		)?
		.json()?;
		field(&registered, "client_id")?.to_owned()
	};
	let scope =
		format!("urn:matrix:client:api:* urn:matrix:client:device:{}", Uuid::new_v4().simple());
	let grant: Value = checked(
		client
			.post(oauth_url("device_authorization_endpoint")?)
			.form(&[("client_id", id.as_str()), ("scope", scope.as_str())])
			.send()?,
	)?
	.json()?;
	let uri = same_origin(server, field(&grant, "verification_uri")?)?;
	let code = field(&grant, "user_code")?;
	ensure!(
		code.len() <= 64
			&& code
				.bytes()
				.all(|b| b.is_ascii_alphanumeric() || b == b'-'),
		"invalid user code"
	);
	eprintln!("Open {uri} and approve device code {code}");
	let timeout = Duration::from_secs(
		grant["expires_in"]
			.as_u64()
			.context("missing grant lifetime")?
			.min(3600),
	);
	let start = Instant::now();
	let mut interval = grant["interval"]
		.as_u64()
		.unwrap_or(5)
		.clamp(1, 3600);
	loop {
		ensure!(
			start.elapsed() + Duration::from_secs(interval) < timeout,
			"device grant expired"
		);
		sleep(Duration::from_secs(interval));
		let response = client
			.post(token_endpoint.clone())
			.form(&[
				("grant_type", DEVICE_GRANT),
				("client_id", id.as_str()),
				("device_code", field(&grant, "device_code")?),
			])
			.send()?;
		let status = response.status();
		let value: Value = response.json()?;
		if !status.is_success() {
			match error_code(&value) {
				| "authorization_pending" => continue,
				| "slow_down" => {
					interval += 5;
					continue;
				},
				| code => bail!("device login failed: HTTP {} ({code})", status.as_u16()),
			}
		}
		let session = Session {
			server: server.clone(),
			client_id: id,
			token_endpoint,
			access_token: field(&value, "access_token")?.into(),
			refresh_token: field(&value, "refresh_token")?.into(),
			expires_at: expires(&value)?,
		};
		save(path, &session)?;
		return Ok(json!({"logged_in":true, "server":server}));
	}
}

pub fn logout(auth: &Auth, path: &Path) -> Result<Value> {
	let _guard = lock(path)?;
	let session: Session = serde_json::from_reader(private_file(path)?)?;
	ensure!(session.server == auth.server, "session belongs to another server");
	let metadata: Value = checked(
		auth.client
			.get(
				auth.server
					.join("/_matrix/client/v1/auth_metadata")?,
			)
			.send()?,
	)?
	.json()?;
	let revoke = same_origin(&auth.server, field(&metadata, "revocation_endpoint")?)?;
	checked(
		auth.client
			.post(revoke)
			.form(&[
				("token", session.access_token.as_str()),
				("token_type_hint", "access_token"),
				("client_id", session.client_id.as_str()),
			])
			.send()?,
	)?;
	fs::remove_file(path)?;
	Ok(json!({"logged_out":true}))
}
