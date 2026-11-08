import gzip
import hashlib
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import materials as m
spec = importlib.util.spec_from_file_location('entry', HERE / 'build-entry.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def tiny_oci(path, extra=False, wrong_diff=False):
    data = {}
    def blob(raw, typ):
        digest = hashlib.sha256(raw).hexdigest()
        data['blobs/sha256/' + digest] = raw
        return {'digest': 'sha256:' + digest, 'size': len(raw), 'mediaType': typ}
    layer = b'real bounded layer bytes'
    layer_desc = blob(gzip.compress(layer), 'application/vnd.oci.image.layer.v1.tar+gzip')
    config = blob(json.dumps({'os': 'linux', 'architecture': 'amd64', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + ('0' * 64 if wrong_diff else hashlib.sha256(layer).hexdigest())]}}).encode(), 'application/vnd.oci.image.config.v1+json')
    manifest = blob(json.dumps({'schemaVersion': 2, 'config': config, 'layers': [layer_desc]}).encode(), 'application/vnd.oci.image.manifest.v1+json')
    data['oci-layout'] = b'{"imageLayoutVersion":"1.0.0"}'
    data['index.json'] = json.dumps({'schemaVersion': 2, 'manifests': [manifest]}).encode()
    if extra:
        data['unreferenced'] = b'unknown'
    with tarfile.open(path, 'w') as tar:
        for name, raw in data.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))
    return manifest['digest']


def candidate(parent, name, text):
    root = parent / name
    (root / 'images').mkdir(parents=True)
    image = root / 'images/example.oci.tar'
    digest = tiny_oci(image)
    (root / 'README.md').write_text(text)
    m.write_json(root / 'image-manifest.json', {'images': [{'file': 'images/example.oci.tar', 'digest': digest, 'bytes': image.stat().st_size, 'sha256': m.sha(image)}]})
    m.seal_inventory(root)
    archive = parent / (name + '.tar.gz')
    m.pack(root, archive)
    return archive


class EntryContract(unittest.TestCase):
    def test_parameters_rejected_before_dispatch(self):
        for env in ({'VERSION': '1; touch bad'}, {'ARCH': 'arm64'}, {'CHANNEL': 'prod'}):
            with self.subTest(env=env), patch.object(e, 'run') as run, self.assertRaises(ValueError):
                e.main('images', env)
            run.assert_not_called()

    def test_slim_requires_both_explicit_inputs(self):
        with tempfile.TemporaryDirectory() as d, patch.object(e.m, 'slim') as slim:
            with self.assertRaises(ValueError):
                e.main('package-slim', {'OUTPUT': d + '/new', 'FULL_PACKAGE': d + '/full'})
            slim.assert_not_called()
            self.assertFalse((Path(d) / 'new').exists())

    def test_existing_and_source_output_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for path in (d, e.ROOT / 'new-output'):
                with self.assertRaises(ValueError):
                    e.output({'OUTPUT': str(path)})
            link = Path(d) / 'link'
            link.symlink_to(Path(d), target_is_directory=True)
            with self.assertRaises(ValueError):
                e.output({'OUTPUT': str(link / 'new')})

    def test_real_check_dispatch_and_failures_propagate(self):
        with patch.object(e, 'run', side_effect=RuntimeError('native gate failure')) as run:
            with self.assertRaises(RuntimeError):
                e.main('check', {})
            self.assertEqual(run.call_count, 1)

    def test_make_rejects_arch_and_shell_version_without_execution(self):
        with tempfile.TemporaryDirectory() as d:
            sentinel = Path(d) / 'not-created'
            result = subprocess.run(['make', 'images', 'VERSION=1.2.3; touch ' + str(sentinel), 'OUTPUT=' + d + '/out'], cwd=e.ROOT, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(sentinel.exists())
            self.assertFalse((Path(d) / 'out').exists())
            result = subprocess.run(['make', 'images', 'ARCH=arm64', 'OUTPUT=' + d + '/out'], cwd=e.ROOT, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(d) / 'out').exists())

    def test_native_chart_version_override_preserves_source_and_app_version(self):
        import yaml
        original = (e.ROOT / 'charts/tuwunel/Chart.yaml').read_bytes()
        with tempfile.TemporaryDirectory() as d:
            result = e.chart_package.package(Path(d) / 'chart', True, version='0.3.1-dev.contract')
            with tarfile.open(Path(d) / 'chart' / result['chart']['file']) as tar:
                chart = yaml.safe_load(tar.extractfile('tuwunel/Chart.yaml').read())
            self.assertEqual(chart['version'], '0.3.1-dev.contract')
            self.assertEqual(chart['appVersion'], yaml.safe_load(original)['appVersion'])
            self.assertEqual((e.ROOT / 'charts/tuwunel/Chart.yaml').read_bytes(), original)

    def test_help_and_unsupported_target(self):
        e.main('help', {})
        with self.assertRaises(ValueError):
            e.main('unknown', {})


class ArchiveContract(unittest.TestCase):
    def test_full_slim_strict_restore(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            base = candidate(p, 'base', 'old instructions')
            full = candidate(p, 'full', 'new instructions')
            m.slim(full, base, p / 'slim')
            slim = p / 'slim/full-slim.tar.gz'
            with self.assertRaises(ValueError):
                m.verify_archive(slim)
            with self.assertRaises(ValueError):
                m.verify_archive(slim, full)
            restored = p / 'restored'
            m.verify_archive(slim, base, restored)
            self.assertEqual(m.inventory(restored), m.inventory(p / 'full'))
            self.assertEqual((restored / 'README.md').read_text(), 'new instructions')
            with self.assertRaises(FileExistsError):
                m.verify_archive(slim, base, restored)

    def test_traversal_link_and_duplicate_rejected(self):
        for names, kind in ((['../escape'], None), (['root/a', 'root/a'], None), (['root/link'], tarfile.SYMTYPE)):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'unsafe.tar'
                with tarfile.open(p, 'w') as t:
                    for name in names:
                        member = tarfile.TarInfo(name)
                        if kind:
                            member.type, member.linkname = kind, '/etc/passwd'
                        t.addfile(member)
                with self.assertRaises(ValueError):
                    m.unpack(p, Path(d) / 'new')
                self.assertFalse((Path(d) / 'escape').exists())

    def test_oci_orphan_and_wrong_uncompressed_layer_rejected(self):
        for fault in ({'extra': True}, {'wrong_diff': True}):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'image.tar'
                digest = tiny_oci(p, **fault)
                with self.assertRaises(ValueError):
                    m.inspect_oci(p, digest)

    def test_missing_or_extra_material_and_immutable_archive(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            archive = candidate(p, 'full', 'docs')
            before = m.sha(archive)
            with self.assertRaises(ValueError):
                m.pack(p / 'full', archive)
            self.assertEqual(m.sha(archive), before)
            (p / 'full/extra').write_text('unexpected')
            with self.assertRaises(ValueError):
                m.verify_tree(p / 'full')

    def test_gzip_trailer_is_validated_by_magic_after_tar_eof(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as tar:
            member = tarfile.TarInfo('root/data')
            member.size = 5
            tar.addfile(member, io.BytesIO(b'hello'))
        compressed = gzip.compress(raw.getvalue())
        corrupt_crc = bytearray(compressed)
        corrupt_crc[-8] ^= 1
        for label, data in [('last4', compressed[:-4]), ('last8', compressed[:-8]), ('crc', bytes(corrupt_crc))]:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                path = Path(d) / 'archive.partial'
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    m.unpack(path, Path(d) / 'out')
                self.assertFalse((Path(d) / 'out/root/data').exists())
        # Extension is not used for compression detection in either direction.
        for name, data in [('compressed.partial', compressed), ('plain.tar.gz', raw.getvalue())]:
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / name
                path.write_bytes(data)
                root = m.unpack(path, Path(d) / 'out')
                self.assertEqual((root / 'data').read_bytes(), b'hello')

    def test_gzip_oci_outer_truncation_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'plain.oci.tar'
            digest = tiny_oci(path)
            truncated = Path(d) / 'oci.partial'
            truncated.write_bytes(gzip.compress(path.read_bytes())[:-4])
            with self.assertRaises(ValueError):
                m.inspect_oci(truncated, digest)

    def test_partial_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / 'tree').mkdir()
            (p / 'tree/a').write_text('data')
            (p / 'new.tar.gz.partial').write_bytes(b'failed prior attempt')
            with self.assertRaises(FileExistsError):
                m.pack(p / 'tree', p / 'new.tar.gz')
            self.assertEqual((p / 'new.tar.gz.partial').read_bytes(), b'failed prior attempt')


if __name__ == '__main__':
    unittest.main()
