#!/usr/bin/env python3
"""Assemble verified external OCI inputs and repository-owned deployment sources."""

import argparse
import gzip
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

import yaml
from package import ROOT, package


def assemble(materials, output, development=False):
    materials, output = Path(materials).resolve(), Path(output).resolve()
    if output == ROOT or ROOT in output.parents or output.exists():
        raise ValueError("output must be a new directory outside the repository")
    lock = json.loads((ROOT / "release/images-amd64.json").read_text())
    values = yaml.safe_load((ROOT / "charts/tuwunel/values.yaml").read_text())
    roles = values["images"]
    if lock["architecture"] != "amd64" or {r["role"] for r in lock["images"]} != set(
        roles
    ):
        raise ValueError("image roles or target architecture differ")
    for image in lock["images"]:
        relative = Path(image["file"])
        path = materials / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or path.is_symlink()
            or not path.is_file()
            or materials not in path.resolve().parents
        ):
            raise ValueError("unsafe image input")
        if (
            path.stat().st_size != image["bytes"]
            or hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
            != image["sha256"]
        ):
            raise ValueError("image archive bytes differ: " + image["role"])
        if any(
            roles[image["role"]][k] != image[k] for k in ("repository", "tag", "digest")
        ):
            raise ValueError("chart image lock differs: " + image["role"])
    output.mkdir(parents=True)
    chart_release = package(output / "chart-build", development)
    stage = output / "material"
    (stage / "charts").mkdir(parents=True)
    chart = output / "chart-build" / chart_release["chart"]["file"]
    shutil.copyfile(chart, stage / "charts" / chart.name)
    for image in lock["images"]:
        target = stage / image["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(materials / image["file"], target)
        if (
            hashlib.file_digest(target.open("rb"), "sha256").hexdigest()
            != image["sha256"]
        ):
            raise ValueError("image changed during copy")
    for source, target in [
        ("release/prepare-values.py", "prepare-values.py"),
        ("release/verify-install.py", "verify-install.py"),
        ("release/site-public.example.json", "site-public.example.json"),
        ("release/images-amd64.json", "image-manifest.json"),
        ("charts/tuwunel/README.md", "README.md"),
        ("operations/backup.sh", "operations/backup.sh"),
    ]:
        destination = stage / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, destination)
        destination.chmod(0o755 if target.endswith(".sh") else 0o644)

    def record(path):
        return {
            "path": path.relative_to(stage).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
        }

    manifest = {
        "schema": "tuwunel.install-materials.v1",
        "development": development,
        "architecture": "amd64",
        "source": chart_release["source"],
        "chart": {**chart_release["chart"], "file": "charts/" + chart.name},
        "images": lock["images"],
        "acceptance": {"materialAssembly": "passed", "clusterInstallation": "not-run"},
        "files": [record(p) for p in sorted(stage.rglob("*")) if p.is_file()],
    }
    (stage / "install-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (stage / "SHA256SUMS").write_text(
        "".join(
            record(p)["sha256"] + "  " + p.relative_to(stage).as_posix() + "\n"
            for p in sorted(stage.rglob("*"))
            if p.is_file()
        )
    )
    archive_path = output / (
        "tuwunel-vasi-" + chart_release["chart"]["version"] + "-amd64.tar.gz"
    )
    with (
        archive_path.open("xb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed,
    ):
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for source in sorted(stage.rglob("*")):
                if not source.is_file():
                    continue
                member = tarfile.TarInfo(
                    "tuwunel-vasi/" + source.relative_to(stage).as_posix()
                )
                member.size, member.mode = (
                    source.stat().st_size,
                    source.stat().st_mode & 0o777,
                )
                with source.open("rb") as stream:
                    archive.addfile(member, stream)
    digest = hashlib.file_digest(archive_path.open("rb"), "sha256").hexdigest()
    Path(str(archive_path) + ".sha256").write_text(
        digest + "  " + archive_path.name + "\n"
    )
    return {
        "archive": str(archive_path),
        "sha256": digest,
        "bytes": archive_path.stat().st_size,
        "clusterInstallation": "not-run",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--materials",
        type=Path,
        required=True,
        help="external directory with images/ OCI archives",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    print(json.dumps(assemble(args.materials, args.output, args.development), indent=2))
