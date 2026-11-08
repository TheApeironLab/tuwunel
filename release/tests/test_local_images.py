"""Native dispatch tests use a fake docker executable, never a daemon/build."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('local_entry', HERE / 'build-entry.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


class LocalImages(unittest.TestCase):
    def test_dispatch_uses_native_source_target_and_sanitizes_publish_environment(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'new'
            with patch.dict(os.environ, {'bake_target': 'publish', 'NO_BAKE': '1', 'CI': 'true', 'CACHIX_AUTH_TOKEN': 'not-a-real-key', 'ATTIC_TOKEN': 'not-a-real-key'}), patch.object(e, 'run', side_effect=RuntimeError('stop before build')) as call:
                with self.assertRaises(RuntimeError):
                    e.build_local_image(out, '1.9.4-dev.1', 'development')
            args, kwargs = call.call_args
            self.assertEqual(args[0], ['bash', e.ROOT / 'docker/bake.sh', 'oci'])
            env = kwargs['env']
            for key in ('bake_target', 'NO_BAKE', 'CACHIX_AUTH_TOKEN', 'ATTIC_TOKEN'):
                self.assertNotIn(key, env)
            self.assertEqual(env['CI'], 'false')
            self.assertEqual(env['package_version'], '1.9.4-dev.1')
            self.assertEqual(env['rust_target'], 'x86_64-unknown-linux-gnu')
            self.assertEqual(env['TUWUNEL_LOCAL_OCI'], str(out / 'homeserver-amd64.oci.tar'))
            intent = json.loads((out / 'build-intent.json').read_text())
            self.assertFalse(intent['push'])
            self.assertEqual(intent['artifact'], 'homeserver-amd64.oci.tar')
            self.assertFalse((out / 'homeserver-image.json').exists())

    def test_actual_shell_local_branch_never_selects_publish_or_load(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'new'
            with patch.object(e, 'run', side_effect=RuntimeError('capture')) as call:
                with self.assertRaises(RuntimeError):
                    e.build_local_image(out, '1.9.4-dev.1', 'development')
            env = call.call_args.kwargs['env']
            tools = Path(d) / 'fake-bin'
            tools.mkdir()
            fake = tools / 'docker'
            fake.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            fake.chmod(0o755)
            env['PATH'] = str(tools) + os.pathsep + os.environ['PATH']
            result = subprocess.run(['bash', e.ROOT / 'docker/bake.sh', 'oci'], cwd=e.ROOT, env=env, capture_output=True, text=True, check=True)
            argv = json.loads(result.stdout)
            self.assertEqual(argv[:2], ['buildx', 'bake'])
            self.assertEqual(argv[-1], 'oci')
            self.assertNotIn('--push', argv)
            self.assertNotIn('--load', argv)
            self.assertNotIn('type=registry', ' '.join(argv))
            self.assertIn('oci*.tags=tuwunel-local:1.9.4-dev.1', argv)
            self.assertIn('oci*.output=type=oci,dest=' + str(out / 'homeserver-amd64.oci.tar') + ',compression=gzip,force-compression=true', argv)
            self.assertIn('*.platform=linux/amd64', argv)
            # An existing artifact must fail before even the fake Docker is called.
            (out / 'homeserver-amd64.oci.tar').write_bytes(b'prior')
            result = subprocess.run(['bash', e.ROOT / 'docker/bake.sh', 'oci'], cwd=e.ROOT, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')

    def test_docker_incompatible_parameters_fail_before_output(self):
        with tempfile.TemporaryDirectory() as d, patch.object(e, 'run') as call:
            for version, name in [('1.2.3+build', 'new'), ('1.2.3', 'comma,name')]:
                with self.assertRaises(ValueError):
                    e.build_local_image(Path(d) / name, version, 'development')
            call.assert_not_called()
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_contract_ci_does_not_inherit_native_builder_initialization(self):
        import yaml
        workflow = yaml.safe_load((e.ROOT / '.github/workflows/build-entrypoints.yml').read_text())
        job = workflow['jobs']['local-contracts']
        self.assertEqual(job['runs-on'], 'ubuntu-latest')
        self.assertNotIn('uses', job)
        self.assertNotIn('container', job)
        commands = [s.get('run', '') for s in job['steps']]
        self.assertIn('python3 -m pip install -r release/requirements.txt', commands)
        self.assertIn('make test TEST_SCOPE=contracts', commands)
        self.assertFalse(any('init.sh' in c or 'bake.sh' in c for c in commands))
        self.assertTrue(any(s.get('uses', '').startswith('azure/setup-helm@') for s in job['steps']))


if __name__ == '__main__':
    unittest.main()
