from __future__ import annotations

import importlib.util
import json
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("generate_site", ROOT / "scripts" / "generate-site.py")
assert SPEC and SPEC.loader
generate_site = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_site)


class GenerateSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name) / "site"
        self.catalog = generate_site.generate(ROOT / "index", self.output)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_aggregate_contains_every_manifest(self) -> None:
        manifest_paths = sorted((ROOT / "index").glob("*/*/*.toml"))
        releases = [release for package in self.catalog["packages"] for release in package["versions"]]
        self.assertEqual(len(releases), len(manifest_paths))
        self.assertEqual({release["path"] for release in releases}, {path.relative_to(ROOT).as_posix() for path in manifest_paths})

    def test_json_preserves_the_complete_manifest(self) -> None:
        aggregate = json.loads((self.output / "crates.json").read_text(encoding="utf-8"))
        for package in aggregate["packages"]:
            package_file = json.loads((self.output / "crates" / f"{package['name']}.json").read_text(encoding="utf-8"))
            self.assertEqual(package_file["package"], package)
            for release in package["versions"]:
                with (ROOT / release["path"]).open("rb") as stream:
                    self.assertEqual(release["manifest"], tomllib.load(stream))

    def test_page_lists_every_package_and_json_endpoint(self) -> None:
        page = (self.output / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="crates.json"', page)
        self.assertIn('data-catalog', page)
        for package in self.catalog["packages"]:
            self.assertIn(f'>{package["name"]}</span>', page)
            self.assertIn(f'crates/{package["name"]}.json', page)


if __name__ == "__main__":
    unittest.main()
