#!/usr/bin/env python3
"""Install this local skill into a Hermes profile, with no downloads or overwrite."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ("SKILL.md", "requirements.txt", "LICENSE", "README.md", "README.zh-CN.md")
DIRECTORIES = ("assets", "references", "scripts", "examples")


def bundle_files():
    for name in ROOT_FILES:
        path = ROOT/name
        if path.is_file():
            yield path
    for name in DIRECTORIES:
        for path in sorted((ROOT/name).rglob("*")):
            relative = path.relative_to(ROOT)
            if (path.is_file() and "__pycache__" not in relative.parts
                    and path.suffix != ".pyc"
                    and relative.parts[:2] != ("examples", "output")):
                yield path


def install(destination, dry_run=False):
    destination = Path(destination).expanduser().resolve()
    if destination == ROOT:
        raise ValueError("The source directory cannot also be the installation target.")
    if destination.exists():
        raise FileExistsError(f"Destination exists; no files were replaced: {destination}")
    missing = [package for package in ("PIL", "numpy") if importlib.util.find_spec(package) is None]
    if missing:
        raise RuntimeError("Install Pillow and NumPy in this interpreter first; no download was attempted.")
    files = list(bundle_files())
    for path in files:
        relative = path.relative_to(ROOT)
        target = (destination/relative).resolve()
        if not target.is_relative_to(destination):
            raise ValueError("A bundled file resolved outside the installation target.")
    if not dry_run:
        destination.mkdir(parents=True, exist_ok=False)
        for path in files:
            target = destination/path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    return {"destination": str(destination), "files": len(files), "dry_run": dry_run}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--hermes-home", type=Path)
    group.add_argument("--destination", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    home = args.hermes_home or Path(os.environ.get("HERMES_HOME", str(Path.home()/".hermes")))
    destination = args.destination or home/"skills"/"tabletop-shadow"
    try:
        print(json.dumps(install(destination, args.dry_run), ensure_ascii=False))
    except (ValueError, FileExistsError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
