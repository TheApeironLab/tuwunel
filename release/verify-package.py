#!/usr/bin/env python3
"""Validate package bytes/OCI or strictly restore a slim package; never install."""
import argparse
from pathlib import Path
import materials
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--archive', type=Path)
p.add_argument('--base-package', type=Path)
p.add_argument('--output', type=Path)
a = p.parse_args()
if a.archive:
    print(materials.verify_archive(a.archive, a.base_package, a.output))
else:
    materials.verify_supported_tree(Path(__file__).resolve().parent)
    print('Materials verified; installation not implied.')
