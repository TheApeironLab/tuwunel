"""Minimal native legacy publisher artifacts, with real OCI byte identities."""
import json
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_entry import candidate, tiny_oci, e, m


def legacy(parent, name):
    root = parent / name
    (root / 'images').mkdir(parents=True)
    image = root / 'images/example.oci.tar'
    digest = tiny_oci(image)
    rows = [{'role': 'example', 'file': 'images/example.oci.tar', 'bytes': image.stat().st_size, 'sha256': m.sha(image), 'digest': digest}]
    # This script must remain data. No legacy verification path runs it.
    (root / 'verify-install.py').write_text("raise RuntimeError('must never execute archived code')\n")
    (root / 'charts').mkdir()
    (root / 'charts/example.tgz').write_bytes(b'bounded chart material fixture')
    m.write_json(root / 'image-manifest.json', {'images': rows})
    files = m.inventory(root)
    declaration = files if m.LEGACY_SCHEMA == 'zygos.install.v1' else [
        {'path': n, 'bytes': (root / n).stat().st_size, 'sha256': h} for n, h in files.items()]
    manifest = {'schema': m.LEGACY_SCHEMA, 'architecture': 'amd64', 'files': declaration}
    if m.LEGACY_SCHEMA == 'tuwunel.install-materials.v1':
        manifest.update({'images': rows, 'chart': {'file': 'charts/example.tgz', 'size': (root / 'charts/example.tgz').stat().st_size, 'sha256': m.sha(root / 'charts/example.tgz')}})
    m.write_json(root / 'install-manifest.json', manifest)
    checksums(root)
    archive = parent / (name + '.tar.gz')
    m.pack(root, archive)
    return root, archive


def checksums(root):
    files = {n: h for n, h in m.inventory(root).items() if n != 'SHA256SUMS'}
    (root / 'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n, h in sorted(files.items())))


class LegacyCompatibility(unittest.TestCase):
    def test_legacy_full_verifies_without_executing_archive_programs(self):
        with tempfile.TemporaryDirectory() as d:
            root, archive = legacy(Path(d), 'old')
            self.assertEqual(m.verify_archive(archive)['materials'], 'passed')
            self.assertFalse((root / 'release-files.json').exists())
            result = subprocess.run(['make', 'verify', 'PACKAGE=' + str(archive)], cwd=e.ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_baseline_slim_roundtrip_preserves_all_new_materials(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            _, baseline = legacy(p, 'old')
            full = candidate(p, 'new', 'new instructions kept')
            m.slim(full, baseline, p / 'slim')
            slim = p / 'slim/new-slim.tar.gz'
            with tarfile.open(slim) as tar:
                lock = json.load(tar.extractfile('new/slim.json'))
                self.assertEqual(set(lock['omitted']), {'images/example.oci.tar'})
                self.assertIsNotNone(tar.getmember('new/README.md'))
            m.verify_archive(slim, baseline, p / 'restored')
            self.assertEqual(m.inventory(p / 'restored'), m.inventory(p / 'new'))
            with self.assertRaises(ValueError):
                m.verify_archive(slim, full)

    def test_declared_hash_size_or_oci_digest_disagreement_rejected_even_with_new_sums(self):
        for kind in ('file', 'image', 'schema'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                root, _ = legacy(Path(d), 'old')
                manifest = json.loads((root / 'install-manifest.json').read_text())
                if kind == 'file':
                    if isinstance(manifest['files'], dict):
                        manifest['files']['verify-install.py'] = '0' * 64
                    else:
                        manifest['files'][0]['bytes'] += 1
                elif kind == 'image':
                    images = json.loads((root / 'image-manifest.json').read_text())
                    images['images'][0]['digest'] = 'sha256:' + '0' * 64
                    (root / 'image-manifest.json').write_text(json.dumps(images))
                    if isinstance(manifest['files'], dict):
                        manifest['files']['image-manifest.json'] = m.sha(root / 'image-manifest.json')
                    else:
                        manifest['images'] = images['images']
                        for row in manifest['files']:
                            if row['path'] == 'image-manifest.json':
                                row.update(bytes=(root / row['path']).stat().st_size, sha256=m.sha(root / row['path']))
                else:
                    manifest['schema'] = 'unknown.publisher.v1'
                (root / 'install-manifest.json').write_text(json.dumps(manifest))
                checksums(root)
                with self.assertRaises(ValueError):
                    m.verify_supported_tree(root)

    def test_extra_file_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root, _ = legacy(Path(d), 'old')
            (root / 'extra').write_bytes(b'not declared')
            checksums(root)
            with self.assertRaises(ValueError):
                m.verify_supported_tree(root)


if __name__ == '__main__':
    unittest.main()
