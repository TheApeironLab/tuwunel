"""Exercise the old Python publisher call with a tiny real OCI and native Chart."""
import importlib.util
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).parent))
from test_entry import tiny_oci
import materials as m
spec = importlib.util.spec_from_file_location('compat_install', HERE / 'package-install.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class InstallCallerCompatibility(unittest.TestCase):
    def test_original_return_paths_archive_root_and_manifest_are_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inputs = root / 'inputs'
            (inputs / 'images').mkdir(parents=True)
            image = inputs / 'images/test.oci.tar'
            digest = tiny_oci(image)
            row = {'role': 'test', 'file': 'images/test.oci.tar', 'digest': digest,
                   'sha256': m.sha(image), 'bytes': image.stat().st_size}
            # Only the large approved-image input boundary is replaced. The actual
            # old wrapper, native Chart packager, assembly and archive verification run.
            with patch.object(publisher.entry, 'records', return_value=[row]), patch.object(publisher.entry, 'run'):
                result = publisher.assemble(inputs, root / 'out', True)
            version, _ = publisher.entry.options({})
            archive = root / 'out' / ('tuwunel-vasi-' + version + '-amd64.tar.gz')
            self.assertEqual(result['archive'], str(archive))
            self.assertEqual(result['sha256'], m.sha(archive))
            self.assertEqual(result['clusterInstallation'], 'not-run')
            self.assertTrue((root / 'out/chart-build/tuwunel-chart-release.json').is_file())
            manifest = m.verify_legacy_tree(root / 'out/material')
            self.assertEqual(manifest['schema'], 'tuwunel.install-materials.v1')
            self.assertEqual(manifest['acceptance']['clusterInstallation'], 'not-run')
            with tarfile.open(archive) as tar:
                self.assertTrue(all(n.startswith('tuwunel-vasi/') for n in tar.getnames()))
                self.assertIsNotNone(tar.getmember('tuwunel-vasi/install-manifest.json'))
            with self.assertRaises(ValueError):
                publisher.assemble(inputs, root / 'out', True)


if __name__ == '__main__':
    unittest.main()
