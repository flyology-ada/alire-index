from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "import_gnat_toolchains", ROOT / "scripts" / "import-gnat-toolchains.py"
)
assert SPEC and SPEC.loader
import_gnat_toolchains = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(import_gnat_toolchains)


class ReleaseTagTests(unittest.TestCase):
    def test_accepts_legacy_major_only_tag(self) -> None:
        self.assertTrue(
            import_gnat_toolchains.tag_matches_version(
                "16.1.0-patchset.1.1.0", "patchset-1.1.0-gcc-16"
            )
        )

    def test_accepts_exact_source_version_tag(self) -> None:
        self.assertTrue(
            import_gnat_toolchains.tag_matches_version(
                "16.2.0-patchset.1.1.0", "patchset-1.1.0-gcc-16.2.0"
            )
        )

    def test_rejects_different_point_release(self) -> None:
        self.assertFalse(
            import_gnat_toolchains.tag_matches_version(
                "16.2.0-patchset.1.1.0", "patchset-1.1.0-gcc-16.1.0"
            )
        )

    def test_rejects_different_patchset(self) -> None:
        self.assertFalse(
            import_gnat_toolchains.tag_matches_version(
                "16.2.0-patchset.1.1.0", "patchset-1.0.1-gcc-16.2.0"
            )
        )

    def test_rejects_partial_source_version_tag(self) -> None:
        self.assertFalse(
            import_gnat_toolchains.tag_matches_version(
                "16.2.0-patchset.1.1.0", "patchset-1.1.0-gcc-16.2"
            )
        )


if __name__ == "__main__":
    unittest.main()
