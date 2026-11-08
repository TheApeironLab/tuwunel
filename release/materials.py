"""Repository-owned release archive operations. No extraction of untrusted paths."""
import gzip
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile

LEGACY_SCHEMA = 'tuwunel.install-materials.v1'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or str(p) != name or '\\' in name:
        raise ValueError('unsafe material path')
    return name


def regular(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError('regular file without symlink ancestors required')
    return path


def inventory(root):
    result = {}
    for p in sorted(Path(root).rglob('*')):
        if p.is_symlink() or not (p.is_file() or p.is_dir()):
            raise ValueError('links and special files are forbidden')
        if p.is_file():
            result[p.relative_to(root).as_posix()] = sha(p)
    return result


@contextmanager
def open_archive(path):
    """Select gzip by magic, including .partial files; otherwise require plain tar."""
    with regular(path).open('rb') as raw:
        magic = raw.read(2)
        raw.seek(0)
        try:
            if magic == b'\x1f\x8b':
                with gzip.GzipFile(fileobj=raw, mode='rb') as expanded:
                    with tarfile.open(fileobj=expanded, mode='r:') as archive:
                        yield archive
            else:
                with tarfile.open(fileobj=raw, mode='r:') as archive:
                    yield archive
        except (EOFError, gzip.BadGzipFile) as exc:
            raise ValueError('invalid or truncated gzip archive') from exc


def drain_gzip_tail(archive):
    # tar EOF is not gzip EOF: force CRC/ISIZE/truncation validation before
    # accepting the inventory. Read in bounded chunks; normal tar record padding
    # is small and zero-filled. Extra hidden payload/oversized padding is refused.
    stream = archive.fileobj
    if isinstance(stream, gzip.GzipFile):
        remaining = 16 * 1024 * 1024
        while True:
            block = stream.read(min(1024 * 1024, remaining + 1))
            if not block:
                break
            remaining -= len(block)
            if remaining < 0 or any(block):
                raise ValueError('unexpected or oversized data after tar EOF')


def members(archive):
    result = {}
    declared_bytes = 0
    for m in archive:
        declared_bytes += m.size
        if len(result) >= 100000 or declared_bytes > 256 * 1024**3:
            raise ValueError('archive exceeds member/expanded-byte budget')
        name = m.name.rstrip('/') if m.isdir() else m.name
        relative(name)
        if name in result or not (m.isfile() or m.isdir()):
            raise ValueError('duplicate, linked or special archive member')
        result[name] = m
    drain_gzip_tail(archive)
    return result


def json_member(archive, table, name):
    m = table[name]
    if not m.isfile() or m.size > 16 * 1024 * 1024:
        raise ValueError('oversized JSON metadata')
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate JSON key')
            result[k] = v
        return result
    return json.loads(archive.extractfile(m).read(), object_pairs_hook=pairs)


def inspect_oci(path, expected=None, details=False):
    """Check every referenced blob, architecture, graph closure and ordered diffIDs."""
    with open_archive(path) as tar:
        table = members(tar)
        used = {'oci-layout', 'index.json'}
        if json_member(tar, table, 'oci-layout') != {'imageLayoutVersion': '1.0.0'}:
            raise ValueError('invalid OCI layout')
        def blob(d):
            digest = d['digest']
            if (not isinstance(digest, str) or not digest.startswith('sha256:')
                or len(digest) != 71 or any(c not in '0123456789abcdef' for c in digest[7:])
                or d.get('urls') or type(d.get('size')) is not int):
                raise ValueError('invalid OCI descriptor')
            name = 'blobs/sha256/' + digest[7:]
            m = table[name]
            if not m.isfile() or m.size != d['size']:
                raise ValueError('OCI blob size mismatch')
            with tar.extractfile(m) as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != digest[7:]:
                raise ValueError('OCI blob hash mismatch')
            used.add(name)
            return name
        index = json_member(tar, table, 'index.json')
        if index.get('schemaVersion') != 2 or len(index['manifests']) != 1:
            raise ValueError('one platform manifest is required')
        descriptor = index['manifests'][0]
        if expected is not None and descriptor['digest'] != expected:
            raise ValueError('OCI image digest mismatch')
        manifest = json_member(tar, table, blob(descriptor))
        if manifest.get('schemaVersion') != 2 or 'config' not in manifest:
            raise ValueError('single image manifest required, not an index')
        config = json_member(tar, table, blob(manifest['config']))
        if (config.get('os'), config.get('architecture')) != ('linux', 'amd64'):
            raise ValueError('only linux/amd64 materials are supported')
        diffs = config['rootfs']['diff_ids']
        if len(diffs) != len(manifest['layers']):
            raise ValueError('layer/diffID count mismatch')
        for layer, diff in zip(manifest['layers'], diffs):
            name = blob(layer)
            typ = layer['mediaType']
            with tar.extractfile(table[name]) as stream:
                if typ.endswith('+gzip') or typ.endswith('.gzip'):
                    with gzip.GzipFile(fileobj=stream) as expanded:
                        actual = hashlib.file_digest(expanded, 'sha256').hexdigest()
                elif typ.endswith('.tar'):
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                else:
                    raise ValueError('unsupported layer compression')
            if 'sha256:' + actual != diff:
                raise ValueError('uncompressed layer digest mismatch')
        if used != {n for n, m in table.items() if m.isfile()}:
            raise ValueError('unreferenced OCI blobs or files')
        identity = {'digest': descriptor['digest'], 'configDigest': manifest['config']['digest'],
                    'layerDigests': [layer['digest'] for layer in manifest['layers']], 'diffIDs': diffs}
        return identity if details else descriptor['digest']


def unpack(path, destination):
    """Extract regular files only into a new private directory; reject collisions."""
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    with open_archive(path) as tar:
        table = members(tar)
        roots = {PurePosixPath(n).parts[0] for n in table}
        if len(roots) != 1:
            raise ValueError('one package root required')
        for name, m in table.items():
            target = destination / name
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output, tar.extractfile(m) as stream:
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                target.chmod(m.mode & 0o755)
        return destination / next(iter(roots))


def verify_tree(root, images=True):
    root = Path(root)
    actual = inventory(root)
    manifest = json.loads((root / 'release-files.json').read_text())
    if manifest['schema'] != 'release-files.v1':
        raise ValueError('unsupported release inventory')
    expected = manifest['files']
    for name in expected:
        relative(name)
    if {k: v for k, v in actual.items() if k not in ('release-files.json', 'SHA256SUMS')} != expected:
        raise ValueError('package inventory/hash mismatch')
    sums = ''.join(f'{h}  {n}\n' for n, h in sorted({**expected, 'release-files.json': sha(root / 'release-files.json')}.items()))
    if (root / 'SHA256SUMS').read_text() != sums:
        raise ValueError('checksum inventory mismatch')
    if images:
        for row in json.loads((root / 'image-manifest.json').read_text())['images']:
            path = root / relative(row['file'])
            if path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
                raise ValueError('image archive mismatch')
            identity = inspect_oci(path, row['digest'], details=True)
            for key in ('configDigest', 'layerDigests', 'diffIDs'):
                if key in row and row[key] != identity[key]:
                    raise ValueError('declared image content identity differs')
    return manifest


def verify_legacy_tree(root):
    """Verify the prior native publisher format without importing its programs."""
    root = Path(root)
    verify_baseline(root)
    manifest = json.loads((root / 'install-manifest.json').read_text())
    if manifest.get('schema') != LEGACY_SCHEMA or manifest.get('architecture') != 'amd64':
        raise ValueError('unsupported legacy package schema/architecture')
    declared = manifest.get('files')
    sizes = {}
    if LEGACY_SCHEMA == 'zygos.install.v1':
        if not isinstance(declared, dict):
            raise ValueError('invalid legacy file declarations')
        expected = declared
    else:
        if not isinstance(declared, list):
            raise ValueError('invalid legacy file declarations')
        expected = {}
        for row in declared:
            name = relative(row['path'])
            if name in expected or type(row.get('bytes')) is not int or row['bytes'] < 0:
                raise ValueError('duplicate/invalid legacy file declaration')
            expected[name] = row['sha256']
            sizes[name] = row['bytes']
    for name in expected:
        relative(name)
    actual = inventory(root)
    if {n: h for n, h in actual.items() if n not in ('install-manifest.json', 'SHA256SUMS')} != expected:
        raise ValueError('legacy declared file inventory differs')
    for name, size in sizes.items():
        if (root / name).stat().st_size != size:
            raise ValueError('legacy declared file size differs')
    images = json.loads((root / 'image-manifest.json').read_text())['images']
    if not images or len({r['role'] for r in images}) != len(images) or len({r['file'] for r in images}) != len(images):
        raise ValueError('duplicate/empty legacy image declarations')
    if LEGACY_SCHEMA == 'tuwunel.install-materials.v1':
        if manifest.get('images') != images:
            raise ValueError('legacy image declarations disagree')
        chart = manifest['chart']
        chart_path = root / relative(chart['file'])
        if chart_path.stat().st_size != chart['size'] or sha(chart_path) != chart['sha256']:
            raise ValueError('legacy Chart declaration differs')
    for image in images:
        path = root / relative(image['file'])
        if image['file'] not in expected or path.stat().st_size != image['bytes'] or sha(path) != image['sha256']:
            raise ValueError('legacy image archive declaration differs')
        identity = inspect_oci(path, image['digest'], details=True)
        for key in ('configDigest', 'layerDigests', 'diffIDs'):
            if key in image and image[key] != identity[key]:
                raise ValueError('legacy image content declaration differs')
    return manifest


def verify_supported_tree(root):
    if (Path(root) / 'release-files.json').is_file():
        return verify_tree(root)
    if (Path(root) / 'install-manifest.json').is_file():
        return verify_legacy_tree(root)
    raise ValueError('unsupported package format: release-files.json or native install-manifest.json required')


def write_json(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def seal_inventory(root):
    files = inventory(root)
    write_json(root / 'release-files.json', {'schema': 'release-files.v1', 'files': files})
    files['release-files.json'] = sha(root / 'release-files.json')
    (root / 'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n, h in sorted(files.items())))


def pack(root, target, archive_root=None):
    archive_root = relative(archive_root or Path(root).name)
    if '/' in archive_root:
        raise ValueError('one archive root component required')
    target = Path(target)
    partial = Path(str(target) + '.partial')
    if target.exists():
        raise ValueError('archive already exists')
    with partial.open('xb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as tar:
            for name in inventory(root):
                p = Path(root) / name
                m = tarfile.TarInfo(archive_root + '/' + name)
                m.size, m.mode = p.stat().st_size, p.stat().st_mode & 0o755
                with p.open('rb') as stream:
                    tar.addfile(m, stream)
    os.link(partial, target)  # Exclusive publication; never rename over a concurrent file.
    partial.unlink()
    with Path(str(target) + '.sha256').open('x') as stream:
        stream.write(sha(target) + '  ' + target.name + '\n')


def slim(full, baseline, output):
    """Only omit byte-identical OCI inputs, pinning the complete baseline archive."""
    output = Path(output)
    output.mkdir(parents=True)  # exclusive; partial failures remain reviewable
    with tempfile.TemporaryDirectory(prefix='release-slim-') as tmp:
        a = unpack(full, Path(tmp) / 'full')
        b = unpack(baseline, Path(tmp) / 'base')
        verify_supported_tree(a)
        # Legacy baselines have their own SHA256SUMS; do not execute their scripts.
        verify_supported_tree(b)
        omissions = {}
        for row in json.loads((a / 'image-manifest.json').read_text())['images']:
            name = relative(row['file'])
            p = b / name
            if p.is_file() and sha(p) == row['sha256']:
                omissions[name] = row['sha256']
        stage = output / a.name
        shutil.copytree(a, stage)
        for name in omissions:
            (stage / name).unlink()
        write_json(stage / 'slim.json', {'schema': 'release-slim.v1', 'baseSha256': sha(baseline), 'omitted': omissions, 'fullFiles': inventory(a)})
        target = output / (a.name + '-slim.tar.gz')
        pack(stage, target)
        verify_archive(target, baseline)


def verify_baseline(root):
    actual = inventory(root)
    lines = (root / 'SHA256SUMS').read_text().splitlines()
    expected = {}
    for line in lines:
        digest, name = line.split('  ', 1)
        relative(name)
        if name in expected or len(digest) != 64:
            raise ValueError('invalid baseline checksum list')
        expected[name] = digest
    if {k: v for k, v in actual.items() if k != 'SHA256SUMS'} != expected:
        raise ValueError('baseline inventory mismatch')


def verify_archive(path, baseline=None, restored=None):
    with tempfile.TemporaryDirectory(prefix='release-verify-') as tmp:
        root = unpack(path, Path(tmp) / 'package')
        if (root / 'slim.json').exists():
            slim_lock = json.loads((root / 'slim.json').read_text())
            if baseline is None or sha(regular(baseline)) != slim_lock['baseSha256']:
                raise ValueError('exact BASE_PACKAGE archive is required')
            base = unpack(baseline, Path(tmp) / 'base')
            verify_supported_tree(base)
            for name, digest in slim_lock['omitted'].items():
                relative(name)
                if not name.startswith('images/') or not name.endswith('.oci.tar') or (root / name).exists():
                    raise ValueError('invalid slim omission')
                if sha(regular(base / name)) != digest:
                    raise ValueError('baseline image mismatch')
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(base / name, root / name)
            (root / 'slim.json').unlink()
            if inventory(root) != slim_lock['fullFiles']:
                raise ValueError('restored package differs from complete inventory')
        verify_supported_tree(root)
        if restored is not None:
            shutil.copytree(root, restored)  # exclusive destination
        return {'materials': 'passed', 'installation': 'not-run'}
