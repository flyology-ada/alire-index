from __future__ import annotations

import importlib.util
import json
import re
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
        self.assertEqual(aggregate["schema_version"], 2)
        for package in aggregate["packages"]:
            package_file = json.loads((self.output / "crates" / f"{generate_site.segment(package['name'])}.json").read_text(encoding="utf-8"))
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
            self.assertIn(f'crates/{package["name"]}/', page)
        self.assertEqual(page.count('class="raw-manifest"'), len(self.catalog["packages"]))
        development_only = sum(package["development_only"] for package in self.catalog["packages"])
        self.assertEqual(page.count("Development only"), development_only)

    def test_published_release_is_selected_over_newer_development(self) -> None:
        releases = [
            {"version": "0.2.0-dev"},
            {"version": "0.1.0"},
        ]
        self.assertEqual(generate_site.select_release(releases)["version"], "0.1.0")
        self.assertTrue(generate_site.is_development_version("0.2.0-dev"))
        self.assertFalse(generate_site.is_development_version("16.1.0-patchset.1.1.0"))

    def test_package_selection_records_published_and_development_status(self) -> None:
        flyology = next(package for package in self.catalog["packages"] if package["name"] == "flyology")
        toolchain = next(package for package in self.catalog["packages"] if package["name"] == "gnat_flyology_native")
        self.assertEqual(flyology["selected_version"], "0.1.0-dev")
        self.assertTrue(flyology["development_only"])
        self.assertEqual(toolchain["selected_version"], "16.1.0-patchset.1.1.0")
        self.assertFalse(toolchain["development_only"])

    def test_crate_and_version_pages_are_generated(self) -> None:
        for package in self.catalog["packages"]:
            name = generate_site.segment(package["name"])
            package_page = (self.output / "crates" / name / "index.html").read_text(encoding="utf-8")
            self.assertIn(package["selected_version"], package_page)
            self.assertIn('Indexed versions', package_page)
            for release in package["versions"]:
                version = generate_site.segment(release["version"])
                version_page = (self.output / "crates" / name / version / "index.html").read_text(encoding="utf-8")
                self.assertIn(f'<span aria-current="page">{release["version"]}</span>', version_page)
                self.assertIn('Complete manifest as JSON', version_page)
                self.assertIn(f'href="../{version}/"', version_page)

    def test_install_command_has_shell_line_continuations(self) -> None:
        page = (self.output / "index.html").read_text(encoding="utf-8")
        command = """alr index \\
  --add=git+https://github.com/flyology-ada/alire-index.git \\
  --name=flyology \\
  --before=community"""
        page_without_option_spans = re.sub(r'</?span(?: class="install-option")?>', "", page)
        self.assertIn(f'<code id="install-command">{command}</code>', page_without_option_spans)
        self.assertEqual(page.count('class="install-option"'), 3)
        self.assertNotIn(".install-panel pre { margin: 0; padding: 1.4rem 1.1rem; overflow-x: auto", page)


if __name__ == "__main__":
    unittest.main()
