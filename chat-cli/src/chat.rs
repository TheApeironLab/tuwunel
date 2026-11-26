use crate::{
	auth::Auth,
	http::{checked, endpoint, error_code, field},
};
use anyhow::{Context, Result, bail, ensure};
use reqwest::{
	Method,
	blocking::{RequestBuilder, Response},
	header::CONTENT_TYPE,
};
use serde_json::{Value, json};
use std::{fs::File, path::Path, time::Duration};
use tempfile::NamedTempFile;
use url::Url;
use uuid::Uuid;

const TRANSFER_TIMEOUT: Duration = Duration::from_secs(3600);

impl Auth<'_> {
	fn request(&self, method: Method, url: Url) -> Result<RequestBuilder> {
		Ok(self
			.client
			.request(method, url)
			.bearer_auth(self.token()?))
	}

	fn v3(&self, parts: &[&str]) -> Result<Url> {
		endpoint(&self.server.join("/_matrix/client/v3/")?, parts)
	}

	fn get(&self, url: Url) -> Result<Value> {
		Ok(checked(self.request(Method::GET, url)?.send()?)?.json()?)
	}

	pub fn whoami(&self) -> Result<Value> {
		self.get(self.v3(&["account", "whoami"])?)
	}

	pub fn rooms(&self) -> Result<Value> {
		self.get(self.v3(&["joined_rooms"])?)
	}

	pub fn history(&self, room: &str, from: Option<String>, limit: u32) -> Result<Value> {
		let mut url = self.v3(&["rooms", room, "messages"])?;
		url.query_pairs_mut()
			.append_pair("dir", "b")
			.append_pair("limit", &limit.to_string())
			.append_pair("filter", r#"{"types":["m.room.message","m.room.encrypted"]}"#);
		if let Some(cursor) = from {
			url.query_pairs_mut().append_pair("from", &cursor);
		}
		self.get(url)
	}

	pub fn sync(&self, since: Option<String>, timeout: u32) -> Result<Value> {
		let mut url = self.v3(&["sync"])?;
		url.query_pairs_mut().append_pair("timeout", &timeout.to_string()).append_pair("filter",
            r#"{"presence":{"types":[]},"account_data":{"types":[]},"room":{"state":{"types":[]},"ephemeral":{"types":[]},"account_data":{"types":[]},"timeline":{"types":["m.room.message","m.room.encrypted"]}}}"#);
		if let Some(cursor) = since {
			url.query_pairs_mut()
				.append_pair("since", &cursor);
		}
		let response: Value = self.get(url)?;
		let mut rooms = serde_json::Map::new();
		if let Some(joined) = response
			.pointer("/rooms/join")
			.and_then(Value::as_object)
		{
			for (room, data) in joined {
				if let Some(timeline) = data.get("timeline") {
					rooms.insert(room.clone(), timeline.clone());
				}
			}
		}
		Ok(json!({"next_batch":field(&response,"next_batch")?, "rooms":rooms}))
	}

	fn plaintext_room(&self, room: &str) -> Result<()> {
		let response = self
			.request(Method::GET, self.v3(&["rooms", room, "state", "m.room.encryption"])?)?
			.send()?;
		if response.status().is_success() {
			bail!("encrypted room: sending requires an encryption-capable client");
		}
		let status = response.status().as_u16();
		let value: Value = response.json().unwrap_or(Value::Null);
		ensure!(
			status == 404 && error_code(&value) == "M_NOT_FOUND",
			"room encryption check: HTTP {status} ({})",
			error_code(&value)
		);
		Ok(())
	}

	fn send_content(&self, room: &str, txn: Option<&str>, content: &Value) -> Result<Value> {
		let generated = Uuid::new_v4().to_string();
		let txn = txn.unwrap_or(&generated);
		let url = self.v3(&["rooms", room, "send", "m.room.message", txn])?;
		let value: Value = checked(
			self.request(Method::PUT, url)?
				.json(content)
				.send()
				.with_context(|| format!("retry this message with --txn-id {txn}"))?,
		)?
		.json()?;
		Ok(json!({"event_id":field(&value,"event_id")?, "txn_id":txn}))
	}

	pub fn send_text(&self, room: &str, txn: Option<&str>, text: String) -> Result<Value> {
		ensure!(!text.is_empty(), "message is empty");
		self.plaintext_room(room)?;
		self.send_content(room, txn, &json!({"msgtype":"m.text","body":text}))
	}

	pub fn upload(&self, room: &str, path: &Path, txn: Option<&str>) -> Result<Value> {
		self.plaintext_room(room)?;
		let name = path
			.file_name()
			.and_then(|name| name.to_str())
			.context("file needs a UTF-8 name")?;
		let file = File::open(path)?;
		let metadata = file.metadata()?;
		ensure!(metadata.is_file(), "upload requires a regular file");
		let mime = mime_guess::from_path(path).first_or_octet_stream();
		let mut url = self.server.join("/_matrix/media/v3/upload")?;
		url.query_pairs_mut()
			.append_pair("filename", name);
		let media: Value = checked(
			self.request(Method::POST, url)?
				.header(CONTENT_TYPE, mime.as_ref())
				.timeout(TRANSFER_TIMEOUT)
				.body(file)
				.send()?,
		)?
		.json()?;
		let mxc = field(&media, "content_uri")?;
		self.send_content(
			room,
			txn,
			&json!({
				"msgtype":"m.file","body":name,"filename":name,"url":mxc,
				"info":{"mimetype":mime.as_ref(),"size":metadata.len()}
			}),
		)
		.context("media uploaded but room message was not confirmed")
	}

	pub fn download(&self, room: &str, event: &str, out: &Path) -> Result<Value> {
		ensure!(!out.try_exists()?, "output file already exists");
		let message = self.get(self.v3(&["rooms", room, "event", event])?)?;
		ensure!(
			message["type"] != "m.room.encrypted" && message["content"].get("file").is_none(),
			"encrypted attachment requires an encryption-capable client"
		);
		ensure!(message["type"] == "m.room.message", "event is not a room message");
		let raw = field(&message["content"], "url")?;
		let (server, id) = raw
			.strip_prefix("mxc://")
			.and_then(|s| s.split_once('/'))
			.context("invalid media URI")?;
		ensure!(
			!server.is_empty()
				&& !id.contains(['/', '?', '#'])
				&& !server.contains(['?', '#', '@']),
			"invalid media URI"
		);
		let url = endpoint(
			&self
				.server
				.join("/_matrix/client/v1/media/download/")?,
			&[server, id],
		)?;
		let mut response: Response = checked(
			self.request(Method::GET, url)?
				.timeout(TRANSFER_TIMEOUT)
				.send()?,
		)?;
		let length = response.content_length();
		let parent = out
			.parent()
			.filter(|p| !p.as_os_str().is_empty())
			.unwrap_or(Path::new("."));
		let mut temp = NamedTempFile::new_in(parent)?;
		let bytes = std::io::copy(&mut response, &mut temp)?;
		ensure!(length.is_none_or(|length| length == bytes), "incomplete download");
		temp.as_file().sync_all()?;
		temp.persist_noclobber(out)?;
		Ok(json!({"event_id":event,"bytes":bytes,"output":out}))
	}
}
