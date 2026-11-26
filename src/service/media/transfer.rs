use std::{
	io::{BufReader, Seek},
	sync::Arc,
	time::Duration,
};

use bytes::Bytes;
use futures::{Stream, StreamExt, pin_mut};
use object_store::GetResult;
use ruma::{Mxc, UserId, http_headers::ContentDisposition};
use tokio::{
	fs::File,
	io::{AsyncWriteExt, BufWriter},
};
use tuwunel_core::{Err, Result, err};

use super::{Data, Dim, Metadata, Service, thumbnail::sequence_reader};

impl Service {
	/// The anonymous staging file is unlinked by the OS on disconnect or process exit.
	/// It lets us inspect container headers and replay the upload to multiple providers.
	pub async fn store_upload(
		&self,
		mxc: &Mxc<'_>,
		user: &UserId,
		disposition: &ContentDisposition,
		declared: Option<&str>,
		pending: bool,
		input: impl Stream<Item = Result<Bytes>> + Send,
	) -> Result {
		let id = mxc.to_string();
		let _lock = self.federation_mutex.lock(&id).await;
		if pending {
			self.check_pending(mxc, user).await?;
		}

		let file = File::from_std(tempfile::tempfile_in(&self.services.config.database_path)?);
		let mut file = BufWriter::with_capacity(256 * 1024, file);
		let mut remaining = self.services.config.max_request_size;
		pin_mut!(input);
		while let Some(chunk) = input.next().await {
			let chunk = chunk?;
			remaining = remaining
				.checked_sub(chunk.len())
				.ok_or_else(|| err!(Request(TooLarge("Media exceeds upload limit"))))?;
			file.write_all(&chunk).await?;
		}
		file.flush().await?;
		let file = file.into_inner().into_std().await;
		let (file, sequence) = tokio::task::spawn_blocking(move || -> std::io::Result<_> {
			let mut reader = BufReader::new(file);
			reader.rewind()?;
			let sequence = sequence_reader(&mut reader)?;
			Ok((reader.into_inner(), sequence))
		})
		.await??;
		let mut file = File::from_std(file);
		if pending {
			self.check_pending(mxc, user).await?;
		}
		let key = Data::file_key(
			mxc,
			&Dim::default(),
			Some(disposition),
			sequence.stored_type(declared),
		)?;
		let path = self.get_media_name_sha256(&key);
		let targets = &self.services.config.store_media_on_providers;
		let mut completed: Vec<&Arc<crate::storage::Provider>> = Vec::new();
		for provider in self
			.storage_providers()
			.filter(|p| targets.is_empty() || targets.contains(&p.name))
		{
			if let Err(error) = provider.put_file(&path, &mut file).await {
				for previous in completed {
					let _ = previous.delete_one(&path).await;
				}
				return Err(error);
			}
			completed.push(provider);
		}
		if completed.is_empty() {
			return Err!(Database("No media storage provider configured"));
		}
		self.db
			.publish_file(mxc, Some(user), &key, pending);
		if pending {
			self.notify_pending(mxc)?;
		}
		Ok(())
	}

	/// Open the object before sending HTTP headers so absent replicas can fall through.
	pub async fn open_stored(
		&self,
		mxc: &Mxc<'_>,
		timeout: Duration,
	) -> Option<(Metadata, GetResult)> {
		let metadata = match self.get_metadata(mxc).await {
			| Some(meta) => meta,
			| None => {
				let _ = self.wait_pending(mxc, timeout).await;
				self.get_metadata(mxc).await?
			},
		};
		let path = self.get_media_name_sha256(&metadata.key);
		for provider in self.storage_providers() {
			if let Ok(object) = provider.load(&path).await {
				return Some((metadata, object));
			}
		}
		None
	}
}
