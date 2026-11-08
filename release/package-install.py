#!/usr/bin/env python3
"""Compatibility entry: assemble a new verified installation package outside the checkout."""
import argparse
import importlib.util
from pathlib import Path
import json

spec = importlib.util.spec_from_file_location('build_entry', Path(__file__).with_name('build-entry.py'))
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def assemble(materials, output, development=False):
    version, _ = entry.options({})
    out = entry.output({'OUTPUT': str(output)})
    return entry.assemble(Path(materials), out, version, 'development' if development else 'release')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--materials', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--development', action='store_true')
    a = p.parse_args()
    print(json.dumps(assemble(a.materials, a.output, a.development), indent=2))
