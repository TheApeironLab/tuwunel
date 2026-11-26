use anyhow::{Context, Result, bail, ensure};
use reqwest::{
	Certificate,
	blocking::{Client, Response},
	redirect::Policy,
};
use serde_json::Value;
use std::{fs, path::Path, time::Duration};
use url::{Host, Url};

pub fn client(ca: Option<&Path>) -> Result<Client> {
	let mut builder = Client::builder()
		.redirect(Policy::none())
		.connect_timeout(Duration::from_secs(15))
		.timeout(Duration::from_secs(60));
	if let Some(path) = ca {
		for cert in Certificate::from_pem_bundle(&fs::read(path)?)? {
			builder = builder.add_root_certificate(cert);
		}
	}
	Ok(builder.build()?)
}

pub fn origin(url: &Url) -> Result<()> {
	let local = match url.host() {
		| Some(Host::Ipv4(ip)) => ip.is_loopback(),
		| Some(Host::Ipv6(ip)) => ip.is_loopback(),
		| Some(Host::Domain("localhost")) => true,
		| _ => false,
	};
	ensure!(
		url.host().is_some() && (url.scheme() == "https" || local && url.scheme() == "http"),
		"expected HTTPS (HTTP is allowed on loopback)"
	);
	ensure!(
		url.username().is_empty()
			&& url.password().is_none()
			&& url.query().is_none()
			&& url.fragment().is_none()
			&& url.path() == "/",
		"expected a server origin"
	);
	Ok(())
}

pub fn endpoint(base: &Url, parts: &[&str]) -> Result<Url> {
	let mut url = base.clone();
	let mut segments = url
		.path_segments_mut()
		.map_err(|_| anyhow::anyhow!("invalid base URL"))?;
	segments.pop_if_empty();
	for part in parts {
		ensure!(!part.is_empty() && !matches!(*part, "." | ".."), "invalid path segment");
		segments.push(part);
	}
	drop(segments);
	Ok(url)
}

pub fn checked(response: Response) -> Result<Response> {
	let status = response.status();
	if status.is_success() {
		return Ok(response);
	}
	let value = response.json::<Value>().unwrap_or(Value::Null);
	let code = error_code(&value);
	bail!("HTTP {} ({code})", status.as_u16());
}

pub fn error_code(value: &Value) -> &str {
	value["errcode"]
		.as_str()
		.or_else(|| value["error"].as_str())
		.filter(|s| {
			s.len() <= 64
				&& s.bytes()
					.all(|b| b.is_ascii_alphanumeric() || b == b'_')
		})
		.unwrap_or("request_failed")
}

pub fn field<'a>(value: &'a Value, name: &str) -> Result<&'a str> {
	value[name]
		.as_str()
		.filter(|s| !s.is_empty())
		.with_context(|| format!("missing {name}"))
}
