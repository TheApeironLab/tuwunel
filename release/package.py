#!/usr/bin/env python3
"""Package the tuwunel application chart reproducibly; outputs always live outside the source tree."""

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def package(output, development=False, name="tuwunel"):
    if name != "tuwunel":
        raise ValueError("unknown application chart")
    output = Path(output).resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("release output must be outside the repository")
    revision = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"])
    )
    if dirty and not development:
        raise ValueError(
            "release requires clean committed sources; use --development for local validation"
        )
    chart = ROOT / "charts" / name
    provenance = {
        "repository": "TheApeironLab/tuwunel",
        "commit": revision,
        "development": development,
        "dirty": dirty,
    }
    inputs = sorted(
        p
        for base in (chart, ROOT / "release")
        for p in base.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    if any(p.is_symlink() for p in inputs):
        raise ValueError("release sources may not contain symlinks")
    source_files = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in inputs
    }
    provenance["sourceSha256"] = hashlib.sha256(
        json.dumps(source_files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    provenance["files"] = source_files
    version = re.search(
        r"^version: ([\w.+-]+)$", (chart / "Chart.yaml").read_text(), re.M
    )[1]
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{name}-{version}.tgz"
    receipt = output / "tuwunel-chart-release.json"
    if target.exists() or receipt.exists():
        raise ValueError("refusing to overwrite existing release material")
    if (chart / "files/provenance.json").exists():
        raise ValueError("files/provenance.json is reserved for build provenance")
    # Normalize timestamps, ownership and archive ordering, including gzip headers.
    with (
        target.open("xb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz,
    ):
        with tarfile.open(fileobj=gz, mode="w") as archive:
            for source in sorted(chart.rglob("*")):
                if source.is_symlink():
                    raise ValueError("chart sources may not contain symlinks")
                if (
                    not source.is_file()
                    or "__pycache__" in source.parts
                    or source.suffix == ".pyc"
                ):
                    continue
                data = source.read_bytes()
                member = tarfile.TarInfo(
                    name + "/" + source.relative_to(chart).as_posix()
                )
                member.size = len(data)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(data))
            data = (json.dumps(provenance, sort_keys=True) + "\n").encode()
            member = tarfile.TarInfo(name + "/files/provenance.json")
            member.size, member.mode = len(data), 0o644
            archive.addfile(member, io.BytesIO(data))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {
        "schema": "tuwunel.chart.release.v1",
        "source": provenance,
        "chart": {
            "name": name,
            "version": version,
            "file": target.name,
            "sha256": digest,
            "size": target.stat().st_size,
        },
    }
    receipt.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chart", choices=["tuwunel"], default="tuwunel")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    print(json.dumps(package(args.output, args.development, args.chart), indent=2))
