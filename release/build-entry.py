#!/usr/bin/env python3
"""Local Matrix application release entrypoints; existing Cargo/Bake interfaces remain."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import materials as m
import package as chart_package

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
TARGETS = ('help', 'check', 'test', 'images', 'chart', 'package', 'package-slim', 'verify', 'materials')


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], cwd=ROOT, check=True, **kwargs)


def options(env):
    import yaml
    native = yaml.safe_load((ROOT / 'charts/tuwunel/Chart.yaml').read_text())['version']
    version = env.get('VERSION') or native
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?', version):
        raise ValueError('VERSION must be SemVer')
    if env.get('CHANNEL', 'development') not in ('development', 'release'):
        raise ValueError('CHANNEL must be development or release')
    if env.get('ARCH', 'amd64') != 'amd64':
        raise ValueError('only ARCH=amd64 is supported')
    return version, env.get('CHANNEL', 'development')


def required(env, key):
    if not env.get(key):
        raise ValueError(key + ' is required')
    return Path(env[key]).absolute()


def output(env):
    path = required(env, 'OUTPUT')
    if path.exists() or path.is_symlink() or ROOT == path.resolve() or ROOT in path.resolve().parents:
        raise ValueError('OUTPUT must be new and outside the source tree')
    if any(p.is_symlink() for p in path.parents):
        raise ValueError('OUTPUT symlink ancestors are forbidden')
    return path


def records(inputs):
    lock = json.loads((HERE / 'images-amd64.json').read_text())
    supplied = inputs / 'image-manifest.json'
    rows = json.loads(supplied.read_text())['images'] if supplied.is_file() else lock['images']
    import yaml
    values = yaml.safe_load((ROOT / 'charts/tuwunel/values.yaml').read_text())['images']
    by_role = {r['role']: r for r in lock['images']}
    if set(values) != set(by_role) or any(values[role][k] != row[k] for role, row in by_role.items() for k in ('repository', 'tag', 'digest')):
        raise ValueError('Chart and image lock differ')
    if len(rows) != len(by_role) or {r['role'] for r in rows} != set(by_role):
        raise ValueError('image role set differs')
    for row in rows:
        wanted = by_role[row['role']]
        if any(row[k] != wanted[k] for k in ('file', 'repository', 'tag', 'digest')):
            raise ValueError('pinned image identity differs')
        try:
            path = m.regular(inputs / m.relative(row['file']))
        except ValueError as exc:
            raise ValueError('unsafe image input') from exc
        if path.stat().st_size != row['bytes'] or m.sha(path) != row['sha256']:
            raise ValueError('image archive bytes differ')
        identity = m.inspect_oci(path, row['digest'], details=True)
        for key in ('configDigest', 'layerDigests', 'diffIDs'):
            if key in row and row[key] != identity[key]:
                raise ValueError('image content identity differs')
            row[key] = identity[key]
    return rows


def assemble(inputs, out, version, channel):
    rows = records(inputs)
    run(['helm', 'lint', 'charts/tuwunel', '--values', 'charts/tuwunel/values-vasi.example.yaml'])
    # Native chart publisher retains source manifest/clean-release policy.
    out.mkdir(parents=True)
    receipt = chart_package.package(out / 'chart-build', channel == 'development', version=version)
    stage = out / 'material'
    (stage / 'charts').mkdir(parents=True)
    shutil.copyfile(out / 'chart-build' / receipt['chart']['file'], stage / 'charts' / receipt['chart']['file'])
    for row in rows:
        dest = stage / row['file']
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inputs / row['file'], dest)
    for source, target in (
        ('release/prepare-values.py', 'prepare-values.py'),
        ('release/verify-install.py', 'verify-install.py'),
        ('release/materials.py', 'materials.py'),
        ('release/verify-package.py', 'verify-package.py'),
        ('release/site-public.example.json', 'site-public.example.json'),
        ('charts/tuwunel/README.md', 'README.md'),
        ('release/BUILD.md', 'BUILD.md'),
        ('operations/backup.sh', 'operations/backup.sh'),
    ):
        dest = stage / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, dest)
        dest.chmod(0o755 if target.endswith('.sh') else 0o644)
    readme = (stage / 'README.md').read_text()
    readme = re.sub(r'当前 Chart 候选为 `[^`]+`', '当前 Chart 候选为 `' + version + '`', readme, count=1)
    for old, new in (('release/images-amd64.json', 'image-manifest.json'),
                     ('release/prepare-values.py', 'prepare-values.py'),
                     ('release/site-public.example.json', 'site-public.example.json')):
        readme = readme.replace(old, new)
    (stage / 'README.md').write_text('> 本归档版本以 package.json 为准，镜像身份以 image-manifest.json 为准。安装未运行。\n'
        '> 构建/完整与精简包校验见 BUILD.md；下文历史验收不代表本归档通过。\n\n' + readme)
    m.write_json(stage / 'image-manifest.json', {'architecture': 'amd64', 'images': rows})
    m.write_json(stage / 'package.json', {'application': 'tuwunel', 'version': version, 'channel': channel,
        'architecture': 'amd64', 'source': receipt['source'], 'installation': 'not-run'})
    # Keep the original package-install output schema and relative paths.
    files = m.inventory(stage)
    m.write_json(stage / 'install-manifest.json', {'schema': 'tuwunel.install-materials.v1',
        'development': channel == 'development', 'architecture': 'amd64',
        'source': receipt['source'], 'chart': {**receipt['chart'], 'file': 'charts/' + receipt['chart']['file']},
        'images': rows, 'acceptance': {'materialAssembly': 'passed', 'clusterInstallation': 'not-run'},
        'files': [{'path': n, 'bytes': (stage / n).stat().st_size, 'sha256': h} for n, h in files.items()]})
    files['install-manifest.json'] = m.sha(stage / 'install-manifest.json')
    (stage / 'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n, h in sorted(files.items())))
    m.verify_supported_tree(stage)
    target = out / ('tuwunel-vasi-' + version + '-amd64.tar.gz')
    m.pack(stage, target, archive_root='tuwunel-vasi')
    m.verify_archive(target)
    return {'archive': str(target), 'sha256': m.sha(target), 'bytes': target.stat().st_size, 'clusterInstallation': 'not-run'}


def build_local_image(out, version, channel):
    # Docker tags cannot contain SemVer build metadata; reject instead of silently
    # changing the requested VERSION. Chart/package still support full SemVer.
    if '+' in version or len(version) > 128 or ',' in str(out) or '\n' in str(out):
        raise ValueError('images requires a Docker-tag-compatible VERSION and OUTPUT without comma/newline')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT))
    if channel == 'release' and dirty:
        raise ValueError('CHANNEL=release requires clean committed sources')
    # Do not inherit BAKE target overrides, NO_BAKE, CI, release publishing settings
    # or cache-upload credentials. Keep only Docker client connection configuration.
    build_env = {k: os.environ[k] for k in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH') if k in os.environ}
    artifact = out / 'homeserver-amd64.oci.tar'
    build_env.update({'TUWUNEL_LOCAL_OCI': str(artifact), 'CI': 'false',
        'GITHUB_ACTOR': 'owo', 'GITHUB_REPOSITORY': 'local/tuwunel',
        'GITHUB_REF': revision, 'GITHUB_REF_NAME': version, 'GITHUB_REF_SHA': revision,
        'package_version': version, 'package_revision': revision,
        'cargo_profile': 'release', 'feat_set': 'default', 'rust_toolchain': 'stable',
        'rust_target': 'x86_64-unknown-linux-gnu', 'sys_name': 'debian',
        'sys_version': 'testing-slim', 'sys_target': 'x86_64-v1-linux-gnu'})
    diff_sha = hashlib.sha256(subprocess.check_output(['git', 'diff', '--binary', 'HEAD'], cwd=ROOT)).hexdigest()
    untracked = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
    overlay = {name: m.sha(m.regular(ROOT / name)) for name in untracked}
    out.mkdir(parents=True)
    m.write_json(out / 'build-intent.json', {'target': 'oci', 'sourceCommit': revision,
        'sourceDirty': dirty, 'sourceDiffSHA256': diff_sha, 'untrackedFiles': overlay,
        'requestedVersion': version, 'architecture': 'amd64',
        'channel': channel, 'push': False, 'artifact': artifact.name})
    run(['bash', ROOT / 'docker/bake.sh', 'oci'], env=build_env)
    identity = m.inspect_oci(artifact, details=True)
    m.write_json(out / 'homeserver-image.json', {**identity, 'file': artifact.name,
        'sha256': m.sha(artifact), 'bytes': artifact.stat().st_size,
        'sourceCommit': revision, 'sourceDirty': dirty, 'sourceDiffSHA256': diff_sha,
        'buildIntentSHA256': m.sha(out / 'build-intent.json'), 'requestedVersion': version,
        'installation': 'not-run', 'distributionImageLockUpdated': False})


def main(target, env=os.environ):
    if target == 'help':
        print('Targets: ' + ' '.join(TARGETS))
        print('Inputs: VERSION CHANNEL=development|release ARCH=amd64 OUTPUT BASE_PACKAGE')
        print('package: MATERIALS; slim: FULL_PACKAGE + BASE_PACKAGE; verify: PACKAGE [BASE_PACKAGE] [OUTPUT]')
        print('images builds this checkout homeserver using native Bake, local OCI only. materials exports pinned distribution dependencies.')
        print('TEST_SCOPE=contracts runs deployment regressions only; default all also runs Cargo workspace tests. See release/BUILD.md.')
        print('Dependencies: Python 3.11+, PyYAML, Git, Helm; check: Cargo/rustfmt; full test: pinned Rust toolchain/native libraries.')
        print('images: Bash, Docker Buildx and configured owo builder; materials: Skopeo. verify/slim: Python stdlib plus archive expansion disk.')
        print('VERSION defaults to Chart.yaml; images rejects + metadata; OUTPUT must be new/outside source. All outputs are local, no publish.')
        return
    version, channel = options(env)
    if target == 'check':
        run(['cargo', 'fmt', '--all', '--check'])
        run(['helm', 'lint', 'charts/tuwunel', '--values', 'charts/tuwunel/values-vasi.example.yaml'])
        return
    if target == 'test':
        scope = env.get('TEST_SCOPE', 'all')
        if scope not in ('all', 'contracts'):
            raise ValueError('TEST_SCOPE must be all or contracts')
        run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests/chart')])
        run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(HERE / 'tests')])
        if scope == 'all':
            run(['cargo', 'test', '--workspace', '--locked'])
        return
    if target == 'verify':
        dest = output(env) if env.get('OUTPUT') else None
        print(json.dumps(m.verify_archive(required(env, 'PACKAGE'), env.get('BASE_PACKAGE'), dest)))
        return
    out = output(env)
    if target == 'package-slim':
        m.slim(required(env, 'FULL_PACKAGE'), required(env, 'BASE_PACKAGE'), out)
    elif target == 'package':
        print(json.dumps(assemble(required(env, 'MATERIALS'), out, version, channel)))
    elif target == 'chart':
        run(['helm', 'lint', 'charts/tuwunel', '--values', 'charts/tuwunel/values-vasi.example.yaml'])
        out.mkdir(parents=True)
        chart_package.package(out, channel == 'development', version=version)
    elif target == 'images':
        build_local_image(out, version, channel)
    elif target == 'materials':
        lock = json.loads((HERE / 'images-amd64.json').read_text())
        out.mkdir(parents=True)
        rows = []
        for row in lock['images']:
            dest = out / m.relative(row['file'])
            dest.parent.mkdir(parents=True, exist_ok=True)
            run(['skopeo', 'copy', '--preserve-digests', '--override-os', 'linux', '--override-arch', 'amd64',
                 'docker://' + row['repository'] + '@' + row['digest'], 'oci-archive:' + str(dest)])
            identity = m.inspect_oci(dest, row['digest'], details=True)
            rows.append({**row, **identity, 'bytes': dest.stat().st_size, 'sha256': m.sha(dest)})
        m.write_json(out / 'image-manifest.json', {'architecture': 'amd64', 'images': rows})
    else:
        raise ValueError('unsupported target')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=TARGETS)
    try:
        main(parser.parse_args().target)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print('build entry failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
