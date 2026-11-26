//! Sniffs containers for animation.
//!
//! A picture names its own format in its first bytes and carries its frame
//! sequence, if it has one, at a place each format fixes. Reading those is what
//! lets a request for a still be honored against a file whose declared content
//! type says otherwise, or says nothing useful, as `image/png` does for an
//! APNG.
//!
//! A walk answers one of three states, because the two questions asked of it
//! take opposite defaults. Whether a picture may be served fails closed, so an
//! unreadable one is withheld and a wrong answer costs a needless re-encode
//! rather than a violation; what a picture is names nothing the walk did not
//! settle, since a guess there would be written down as fact.

use std::io::{self, Cursor, Read, Seek, SeekFrom};

use tuwunel_core::implement;

use super::animate::{APNG, GIF, WEBP};

// Leading signatures of the three animating containers.
const PNG_MAGIC: &[u8] = b"\x89PNG\r\n\x1a\n";
const RIFF_MAGIC: &[u8] = b"RIFF";
const WEBP_MAGIC: &[u8] = b"WEBP";
const GIF_MAGIC: [&[u8]; 2] = [b"GIF87a", b"GIF89a"];

// Chunk names that settle a PNG.
//
// The animation control is required to precede the pixel data, so meeting
// the data first settles the question without reading any of it.
const PNG_ANIMATION: &[u8] = b"acTL";
const PNG_DATA: &[u8] = b"IDAT";

// Chunk names a WebP can open with, and the animation flag's own bit.
//
// Only the extended form can hold a frame sequence, so a file opening with a
// plain lossy or lossless chunk is a still without reading further.
const WEBP_EXTENDED: &[u8] = b"VP8X";
const WEBP_LOSSY: &[u8] = b"VP8 ";
const WEBP_LOSSLESS: &[u8] = b"VP8L";
const WEBP_ANIMATION: u8 = 0x02;

// Leading byte of each block in a GIF body.
//
// A body is a flat sequence of these, ending at the trailer, and animation
// is the presence of a second image rather than anything the format states.
const GIF_EXTENSION: u8 = 0x21;
const GIF_IMAGE: u8 = 0x2C;
const GIF_TRAILER: u8 = 0x3B;

// GIF colour-table flag and size bits.
//
// The flag says whether one follows and the size field holds the exponent of
// its entry count, each entry being a three byte colour.
const GIF_COLOR_TABLE: u8 = 0x80;
const GIF_TABLE_SIZE: u8 = 0x07;

/// Bytes the signature and the logical screen descriptor occupy together.
///
/// A global colour table, when one is announced, follows them, and the block
/// sequence follows that.
const GIF_HEADER_LEN: usize = 13;

/// What a container's header settles about a frame sequence.
///
/// The two questions a caller asks of it want opposite defaults, so an
/// unsettled walk is its own answer rather than being folded into either.
#[derive(Clone, Copy)]
pub(in super::super) enum Sequence {
	/// This picture holds no sequence, either because its container cannot or
	/// because the walk reached the end of one.
	Absent,

	/// The walk found a sequence, in a container of this type.
	Present(&'static str),

	/// The walk ran out of picture, or met a structure it does not know.
	Unsettled,
}

/// Whether these bytes may carry more than one frame.
///
/// Walks the container first, for a caller holding no walk of its own.
#[inline]
pub(in super::super) fn animates(bytes: &[u8]) -> bool { sequence(bytes).animates() }

/// Whether the walked picture may carry more than one frame.
///
/// Only a settled walk answers false, so a truncated or unrecognized picture is
/// withheld from a request that forbade animation rather than served to it.
#[implement(Sequence)]
#[inline]
pub(in super::super) fn animates(self) -> bool { !matches!(self, Self::Absent) }

/// Whether the walk settled on an animation rather than merely allowing one.
///
/// This is the narrower half of [`Self::animates`], which withholds an
/// unsettled walk too: naming a picture takes proof where refusing to serve one
/// does not.
#[cfg(feature = "media_thumbnail")]
#[implement(Sequence)]
#[inline]
pub(in super::super) fn names_animation(self) -> bool { self.animated_type().is_some() }

/// The container type a picture's own bytes name.
///
/// Walks the container first, for a caller holding no walk of its own.
#[inline]
pub(in super::super) fn animated_type(bytes: &[u8]) -> Option<&'static str> {
	sequence(bytes).animated_type()
}

/// The content type the walked picture ought to be stored under.
///
/// A settled walk names the container itself, and the declared type stands
/// where the walk settled nothing. The label is what a lookup goes on wherever
/// the picture is not in hand: choosing between the rows at a size walks keys
/// alone, and a redirect hands the object over without reading it.
#[implement(Sequence)]
#[inline]
pub(in super::super) fn stored_type(self, declared: Option<&str>) -> Option<&str> {
	self.animated_type().or(declared)
}

/// The container type the walked picture's own bytes name.
///
/// Only a settled walk answers `Some`, since this names what a picture is
/// rather than deciding what may be served, and a guess would be recorded as
/// fact.
#[implement(Sequence)]
#[inline]
fn animated_type(self) -> Option<&'static str> {
	match self {
		| Self::Present(content_type) => Some(content_type),
		| Self::Absent | Self::Unsettled => None,
	}
}

/// Walks the container its first bytes name.
///
/// A container that cannot hold a sequence answers `Absent`, and so does one
/// whose walk reaches the end of its blocks without finding a second frame. A
/// walk that runs out of picture, or meets a structure it does not know,
/// answers `Unsettled` instead of guessing either way.
pub(in super::super) fn sequence(bytes: &[u8]) -> Sequence {
	sequence_reader(&mut Cursor::new(bytes)).expect("reading a slice cannot fail")
}

/// Sniff both staged uploads and in-memory thumbnails without loading picture data.
pub(in super::super) fn sequence_reader<R: Read + Seek>(reader: &mut R) -> io::Result<Sequence> {
	let mut header = [0; 21];
	let mut used = 0;
	while used < header.len() {
		let count = reader.read(&mut header[used..])?;
		if count == 0 {
			break;
		}
		used += count;
	}
	let bytes = &header[..used];
	let (kind, settled) = if bytes.starts_with(PNG_MAGIC) {
		reader.seek(SeekFrom::Start(8))?;
		(APNG, png_sequence(reader))
	} else if bytes.starts_with(RIFF_MAGIC) && bytes.get(8..12) == Some(WEBP_MAGIC) {
		(WEBP, Ok(webp_sequence(bytes)))
	} else if GIF_MAGIC
		.iter()
		.any(|magic| bytes.starts_with(magic))
	{
		(GIF, gif_sequence(reader, bytes))
	} else {
		return Ok(Sequence::Absent);
	};
	match settled {
		| Ok(Some(true)) => Ok(Sequence::Present(kind)),
		| Ok(Some(false)) => Ok(Sequence::Absent),
		| Ok(None) => Ok(Sequence::Unsettled),
		| Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => Ok(Sequence::Unsettled),
		| Err(e) => Err(e),
	}
}

fn png_sequence<R: Read + Seek>(reader: &mut R) -> io::Result<Option<bool>> {
	loop {
		let mut length = [0; 4];
		let mut kind = [0; 4];
		reader.read_exact(&mut length)?;
		reader.read_exact(&mut kind)?;
		if kind == PNG_ANIMATION {
			return Ok(Some(true));
		}
		if kind == PNG_DATA {
			return Ok(Some(false));
		}
		reader.seek_relative(i64::from(u32::from_be_bytes(length)) + 4)?;
	}
}

/// Whether a WebP announces animation in its opening chunk.
///
/// The chunk name and the flags sit at fixed offsets, so this reads two fields
/// and never walks. Only the two plain still forms answer as a still, since a
/// chunk name neither they nor the extended header claim is a structure this
/// does not recognize.
fn webp_sequence(bytes: &[u8]) -> Option<bool> {
	let chunk = bytes.get(12..16)?;

	match chunk {
		| _ if chunk == WEBP_LOSSY || chunk == WEBP_LOSSLESS => Some(false),
		| _ if chunk == WEBP_EXTENDED => bytes
			.get(20)
			.map(|flags| flags & WEBP_ANIMATION != 0),
		| _ => None,
	}
}

/// Whether a GIF holds more than one image descriptor.
///
/// Frame count is written nowhere in the format, so the blocks are walked
/// until a second image is found or the trailer ends them. Every block clears
/// at least its own introducer and a terminating sub-block, so the offset
/// rises on every pass and a walk that runs off the picture ends.
fn gif_sequence<R: Read + Seek>(reader: &mut R, header: &[u8]) -> io::Result<Option<bool>> {
	let Some(&screen) = header.get(10) else {
		return Ok(None);
	};
	reader.seek(SeekFrom::Start(GIF_HEADER_LEN as u64 + color_table_len(screen)))?;
	let mut seen = false;
	loop {
		match read_byte(reader)? {
			| GIF_TRAILER => return Ok(Some(false)),
			| GIF_EXTENSION => reader.seek_relative(1)?,
			| GIF_IMAGE if seen => return Ok(Some(true)),
			| GIF_IMAGE => {
				seen = true;
				reader.seek_relative(8)?;
				let packed = read_byte(reader)?;
				reader.seek_relative(color_table_len(packed) as i64 + 1)?;
			},
			| _ => return Ok(None),
		}
		loop {
			let count = read_byte(reader)?;
			if count == 0 {
				break;
			}
			reader.seek_relative(i64::from(count))?;
		}
	}
}

fn read_byte(reader: &mut impl Read) -> io::Result<u8> {
	let mut byte = [0];
	reader.read_exact(&mut byte)?;
	Ok(byte[0])
}

fn color_table_len(packed: u8) -> u64 {
	if packed & GIF_COLOR_TABLE == 0 {
		return 0;
	}
	3u64 << (u32::from(packed & GIF_TABLE_SIZE) + 1)
}
