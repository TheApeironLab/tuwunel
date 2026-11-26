mod auth;
mod chat;
mod http;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use serde_json::Value;
use std::{
	io::{self, Read},
	path::PathBuf,
	process::ExitCode,
};
use url::Url;

#[derive(Parser)]
#[command(
	version,
	about = "Read and send Matrix messages and files as the signed-in Chat user"
)]
struct Cli {
	/// Matrix homeserver origin (required with a host-managed token).
	#[arg(long, env = "CHAT_SERVER", global = true)]
	server: Option<Url>,
	/// Absolute path for a private CLI-managed OAuth session.
	#[arg(
		long,
		env = "CHAT_SESSION",
		global = true,
		conflicts_with = "token_file"
	)]
	session: Option<PathBuf>,
	/// Absolute private file containing a host-managed Matrix access token.
	#[arg(long, env = "CHAT_TOKEN_FILE", global = true)]
	token_file: Option<PathBuf>,
	/// Additional trusted site CA in PEM format.
	#[arg(long, env = "CHAT_CA_FILE", global = true)]
	ca_file: Option<PathBuf>,
	#[command(subcommand)]
	command: Command,
}

#[derive(Subcommand)]
enum Command {
	/// Authorize a new CLI session through the site's device-code SSO.
	Login {
		/// Element origin already allowed by the site for client registration.
		#[arg(
			long,
			conflicts_with = "client_id",
			required_unless_present = "client_id"
		)]
		chat_url: Option<Url>,
		/// Use a previously registered public OAuth client.
		#[arg(long)]
		client_id: Option<String>,
	},
	/// Revoke the CLI session and remove its local credentials.
	Logout,
	/// Show the Matrix identity of the current caller.
	Whoami,
	/// List joined room IDs.
	Rooms,
	/// Read messages newest first. Use the returned end cursor for the next page.
	Read {
		room: String,
		#[arg(long)]
		from: Option<String>,
		#[arg(long,default_value_t=50,value_parser=clap::value_parser!(u32).range(1..=1000))]
		limit: u32,
	},
	/// Fetch one batch of updates. Resume with the previous next_batch cursor.
	Sync {
		#[arg(long)]
		since: Option<String>,
		#[arg(long, default_value_t=0, value_parser=clap::value_parser!(u32).range(0..=30000))]
		timeout_ms: u32,
	},
	/// Send a text message; reads UTF-8 text from stdin when --text is omitted.
	Send {
		room: String,
		#[arg(long)]
		text: Option<String>,
		/// Stable ID to reuse when retrying this particular message.
		#[arg(long)]
		txn_id: Option<String>,
	},
	/// Transfer a file attached to a room message.
	Attachment {
		#[command(subcommand)]
		command: Attachment,
	},
}

#[derive(Subcommand)]
enum Attachment {
	/// Upload a file and send its attachment message to the room.
	Upload {
		room: String,
		file: PathBuf,
		#[arg(long)]
		txn_id: Option<String>,
	},
	/// Download the attachment on an existing event to a new local file.
	Download {
		room: String,
		event: String,
		#[arg(long)]
		out: PathBuf,
	},
}

fn run(cli: Cli) -> Result<Value> {
	let client = http::client(cli.ca_file.as_deref())?;
	if let Command::Login { chat_url, client_id } = cli.command {
		return auth::login(
			&client,
			cli.server
				.as_ref()
				.context("login requires --server")?,
			cli.session
				.as_deref()
				.context("login requires --session")?,
			chat_url,
			client_id,
		);
	}
	let auth =
		auth::Auth::load(client, cli.server, cli.session.as_deref(), cli.token_file.as_deref())?;
	match cli.command {
		| Command::Login { .. } => unreachable!(),
		| Command::Logout => auth::logout(
			&auth,
			cli.session
				.as_deref()
				.context("logout requires a managed --session")?,
		),
		| Command::Whoami => auth.whoami(),
		| Command::Rooms => auth.rooms(),
		| Command::Read { room, from, limit } => auth.history(&room, from, limit),
		| Command::Sync { since, timeout_ms } => auth.sync(since, timeout_ms),
		| Command::Send { room, text, txn_id } => {
			let body = if let Some(body) = text {
				body
			} else {
				let mut buf = String::new();
				io::stdin().read_to_string(&mut buf)?;
				buf
			};
			auth.send_text(&room, txn_id.as_deref(), body)
		},
		| Command::Attachment {
			command: Attachment::Upload { room, file, txn_id },
		} => auth.upload(&room, &file, txn_id.as_deref()),
		| Command::Attachment {
			command: Attachment::Download { room, event, out },
		} => auth.download(&room, &event, &out),
	}
}

fn main() -> ExitCode {
	match run(Cli::parse()) {
		| Ok(value) => {
			println!("{value}");
			ExitCode::SUCCESS
		},
		| Err(err) => {
			eprintln!("{err:#}");
			ExitCode::FAILURE
		},
	}
}
