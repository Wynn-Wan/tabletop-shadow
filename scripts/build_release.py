#!/usr/bin/env python3
"""Build a deterministic skill ZIP and SHA-256 file, excluding local reports."""
import hashlib
from pathlib import Path
import re
import zipfile

from install_skill import ROOT, bundle_files


def main():
    source = (ROOT/"scripts"/"add_shadow.py").read_text(encoding="utf-8")
    version = re.search(r'^VERSION = "([^"]+)"', source, re.MULTILINE).group(1)
    dist = ROOT/"dist"
    dist.mkdir(exist_ok=True)
    destination = dist/f"tabletop-shadow-v{version}.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(bundle_files()):
            name = (Path("tabletop-shadow")/path.relative_to(ROOT)).as_posix()
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    (dist/"SHA256SUMS").write_text(f"{checksum}  {destination.name}\n", encoding="utf-8")
    print(f"{destination.name}  {destination.stat().st_size} bytes\nSHA256 {checksum}")


if __name__ == "__main__":
    main()
