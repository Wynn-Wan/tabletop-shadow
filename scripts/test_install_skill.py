import hashlib
from pathlib import Path
import tempfile
import unittest

from install_skill import install, ROOT


class InstallTests(unittest.TestCase):
    def test_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"custom-profile"/"skills"/"tabletop-shadow"
            report = install(target, dry_run=True)
            self.assertTrue(report["dry_run"])
            self.assertGreater(report["files"], 5)
            self.assertFalse(target.exists())

    def test_copy_is_complete_and_existing_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"skills"/"tabletop-shadow"
            report = install(target)
            self.assertFalse(report["dry_run"])
            for relative in ("SKILL.md", "assets/shadow-style.json", "scripts/add_shadow.py"):
                self.assertEqual(hashlib.sha256((ROOT/relative).read_bytes()).digest(),
                                 hashlib.sha256((target/relative).read_bytes()).digest())
            marker = target/"my-local-file.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                install(target)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_cannot_install_over_source(self):
        with self.assertRaises(ValueError):
            install(ROOT)


if __name__ == "__main__":
    unittest.main()
