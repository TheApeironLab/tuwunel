# Unified local build entrypoints

`make help` lists `help check test images chart package package-slim verify`.
No target publishes, pushes, contacts a cluster or installs an application. Existing
Cargo, `docker/bake.sh`, release/package.py and deployment interfaces remain usable.

Requirements: Python 3.11+, dependencies in release/requirements.txt, Git and Helm.
`check` runs native Cargo formatting and Chart lint; `test` runs the existing Chart
and initializer regressions, release archive tests, then Cargo workspace tests.
`TEST_SCOPE=contracts` explicitly selects just the deployment/package lane. Full
Rust tests require the repository toolchain and native libraries; consult
CONTRIBUTING.md and docker/README.md. Existing CI gates are not replaced.

Inputs:

- VERSION defaults to charts/tuwunel/Chart.yaml. A supplied SemVer sets the packaged
  Chart version for chart/package; images uses it as native package metadata and
  a local image tag. appVersion and distribution image tags/digests remain unchanged.
- CHANNEL=development by default; CHANNEL=release requires clean committed sources.
  Neither channel is installation or production acceptance.
- ARCH=amd64 only. Other architectures/channels fail before any image work.
- OUTPUT is a new directory outside the checkout (no symlink ancestors). Existing
  paths are refused; failed partials remain for inspection, not automatic retry.
- MATERIALS contains the external `images/` OCI archives. Without image-manifest.json
  their bytes must match release/images-amd64.json. With that manifest, the same
  pinned role/repository/tag/digest is required, with its actual archive byte SHA
  and size. No arbitrary upstream update is accepted.
- FULL_PACKAGE and BASE_PACKAGE are explicit full archives for package-slim.
- PACKAGE is the full/slim archive to verify. Optional OUTPUT writes the verified
  restored tree. A slim archive requires its exact BASE_PACKAGE.

```sh
make check
make test TEST_SCOPE=contracts
make images VERSION=1.9.4-dev.1 OUTPUT=/absolute/new-homeserver
make materials OUTPUT=/absolute/new-images
make chart VERSION=0.3.1-dev.1 OUTPUT=/absolute/new-chart
make package VERSION=0.3.1-dev.1 MATERIALS=/absolute/new-images OUTPUT=/absolute/new-package
make package-slim FULL_PACKAGE=/absolute/full.tar.gz BASE_PACKAGE=/absolute/prior-full.tar.gz OUTPUT=/absolute/new-slim
make verify PACKAGE=/absolute/slim.tar.gz BASE_PACKAGE=/absolute/prior-full.tar.gz OUTPUT=/absolute/restored
```

`images` builds **this checkout's homeserver** through the original docker/bake.sh
and native `oci` target. It selects one release/stable/default-feature Debian
GNU x86_64-v1 matrix, linux/amd64, gzip OCI output into the exclusive OUTPUT.
The existing Buildx builder `owo` must be prepared as documented in docker/README.md.
No registry exporter, push, remote cache-upload credentials or CI target is selected.
VERSION sets native package metadata/local image tag; it does not rewrite Cargo.toml
or claim that the binary's source-controlled semantic version changed. SemVer `+`
metadata is rejected for this Docker-tag entrypoint. The output contains durable
build-intent.json, homeserver-amd64.oci.tar and homeserver-image.json with actual
manifest/config/layer/diffID/byte identity after verification. Failed output is not
reused. This does not update the reviewed distribution image lock automatically.

`materials` is a separate optional target: it uses Skopeo to export the five exact
upstream digests in images-amd64.json with --preserve-digests, checking the OCI graph
and uncompressed layers. It does not build repository source. `package` consumes
these reviewed distribution materials; a newly built homeserver must go through an
explicit review/update of images-amd64.json and Chart values before inclusion.
Existing native Bake targets and release interfaces remain unchanged unless the
new explicit local-export mode is selected.

Full packages contain independent instructions/configuration examples, Chart,
operations/backup.sh, all OCI files, source provenance, install-manifest.json and
SHA256SUMS. `python3 verify-package.py` verifies a full extracted package. It can
also restore a slim archive with `--archive`, `--base-package`, `--output`.
First verify the trusted outer archive .sha256; then restore slim, verify full
SHA256SUMS and OCI materials before normal Vasi imports and site configuration.
Follow the packaged README for namespace-local prerequisites, CA/SSO, initialization,
backup and separate actual installation checks. No site credentials are packaged.

Slim omits only byte-identical OCI files from the explicit baseline, preserving all
non-image materials. The complete baseline archive SHA and full restored file set
are fixed. Unsafe members, links, duplicates, extra/missing material and wrong
baseline are refused, without executing scripts from either archive. Prior native
full packages with a complete SHA256SUMS inventory remain valid baselines. Preserve
full archives; slim alone is not an installation or disaster-recovery package.

The old package-install.py arguments remain supported and now use this same
verification/assembly implementation. Output still contains material/, chart-build/ and the original full archive
filename, with archive root tuwunel-vasi/. The install-manifest.json schema is
preserved; package.json adds the current channel and source metadata. No historical
installation claims are copied. Every newly assembled package explicitly
records installation-not-run; sealed prior archives and their evidence are untouched.

Legacy full verification: `make verify PACKAGE=/absolute/old-full.tar.gz` also
accepts this repository's prior native install-manifest schema (and the standalone
verify-package.py has the same behavior). It checks the complete SHA256SUMS file
set, the old manifest's file sizes/hashes where declared, its image declarations
and full OCI content graph. It never imports or executes archived scripts. Unknown
legacy schemas fail explicitly. The same checks apply when using a legacy full
archive as BASE_PACKAGE for slim creation or restoration.
