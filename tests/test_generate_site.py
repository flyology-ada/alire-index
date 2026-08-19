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
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temporary.name) / "site"
        cls.catalog = generate_site.generate(ROOT / "index", cls.output)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_aggregate_contains_every_manifest(self) -> None:
        manifest_paths = sorted((ROOT / "index").glob("*/*/*.toml"))
        releases = [release for package in self.catalog["packages"] for release in package["versions"]]
        self.assertEqual(len(releases), len(manifest_paths))
        self.assertEqual({release["path"] for release in releases}, {path.relative_to(ROOT).as_posix() for path in manifest_paths})

    def test_json_preserves_the_complete_manifest(self) -> None:
        aggregate = json.loads((self.output / "crates.json").read_text(encoding="utf-8"))
        self.assertEqual(aggregate["schema_version"], 3)
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

    def test_llms_file_describes_catalog_and_every_package(self) -> None:
        document = (self.output / "llms.txt").read_text(encoding="utf-8")
        self.assertTrue(document.startswith("# Flyology Crate Index\n\n> "))
        self.assertIn(f"[Aggregate JSON catalog]({generate_site.CANONICAL_URL}crates.json)", document)
        self.assertIn(f"[Change history]({generate_site.CANONICAL_URL}changes/)", document)
        self.assertIn(f"[JSON schema notes]({generate_site.CANONICAL_URL}README.txt)", document)
        for package in self.catalog["packages"]:
            endpoint = (
                f"- [{package['name']}]({generate_site.CANONICAL_URL}crates/"
                f"{generate_site.segment(package['name'])}.json):"
            )
            self.assertIn(endpoint, document)
            self.assertIn(package["description"], document)
        self.assertEqual(document.count("\n- ["), len(self.catalog["packages"]) + 5)

    def test_pages_use_the_flyology_logo(self) -> None:
        home = (self.output / "index.html").read_text(encoding="utf-8")
        changes = (self.output / "changes" / "index.html").read_text(encoding="utf-8")
        crate = (self.output / "crates" / "flyology" / "index.html").read_text(encoding="utf-8")
        mark = (self.output / "flyology-mark.svg").read_text(encoding="utf-8")
        icon = (self.output / "flyology-logo.svg").read_text(encoding="utf-8")
        styles = (self.output / "assets" / "styles" / "index.css").read_text(encoding="utf-8")

        self.assertIn('<img class="brand-mark" src="./flyology-mark.svg" alt="">', home)
        self.assertIn('<link rel="icon" href="flyology-logo.svg" type="image/svg+xml">', home)
        self.assertIn(f'href="assets/styles/index.css?v={generate_site.INDEX_CSS_VERSION}"', home)
        self.assertIn('<img class="brand-mark" src="../flyology-mark.svg" alt="">', changes)
        self.assertIn(f'href="../assets/styles/index.css?v={generate_site.INDEX_CSS_VERSION}"', changes)
        self.assertIn('<img class="brand-mark" src="../../flyology-mark.svg" alt="">', crate)
        self.assertIn(f'href="../../assets/styles/index.css?v={generate_site.INDEX_CSS_VERSION}"', crate)
        self.assertIn("Flyology transparent mark", mark)
        self.assertNotIn('<rect x="12" y="12"', mark)
        self.assertIn("Flyology primary icon", icon)
        self.assertNotIn(':root[data-theme="dark"] .brand img.brand-mark { filter: none; }', styles)

    def test_home_page_has_a_bounded_change_preview(self) -> None:
        page = (self.output / "index.html").read_text(encoding="utf-8")
        history = generate_site.load_change_history(self.catalog)
        entry_count = sum(len(group["entries"]) for group in history)
        self.assertIn('href="changes/"', page)
        self.assertIn('id="changes-preview-title"', page)
        self.assertEqual(
            page.count('class="change-entry '),
            min(generate_site.HOME_CHANGE_LIMIT, entry_count),
        )

    def test_detailed_change_history_is_generated_from_git(self) -> None:
        page = (self.output / "changes" / "index.html").read_text(encoding="utf-8")
        history = generate_site.load_change_history(self.catalog)
        self.assertTrue(history)
        self.assertIn('<a href="../changes/" aria-current="page">Changes</a>', page)
        self.assertIn("Index changes.", page)
        self.assertIn(f"{generate_site.REPOSITORY_URL}/commit/", page)
        self.assertIn(history[0]["commit"][:8], page)

    def test_change_entry_distinguishes_publication_and_dev_update(self) -> None:
        before = {
            "name": "example",
            "version": "0.2.0-dev",
            "depends-on": [{"base": "^1"}],
            "origin": {"url": "git+https://github.com/example/example.git", "commit": "a" * 40},
        }
        after = {
            **before,
            "depends-on": [{"base": "^2"}, {"new_dependency": "*"}],
            "origin": {"url": "git+https://github.com/example/example.git", "commit": "b" * 40},
        }
        updated = generate_site.change_entry("M", "index/ex/example/example-0.2.0-dev.toml", before, after)
        published = generate_site.change_entry("A", "index/ex/example/example-0.2.0-dev.toml", None, after)
        assert updated and published
        self.assertEqual(updated["kind"], "development")
        self.assertEqual(updated["changed_fields"], ["depends-on", "origin"])
        self.assertEqual(
            [(change["kind"], change["name"]) for change in updated["dependency_changes"]],
            [("changed", "base"), ("added", "new_dependency")],
        )
        self.assertEqual(published["kind"], "published")
        self.assertEqual(published["changed_fields"], [])
        updated_html = generate_site.render_change_entry(
            updated, root_prefix="", detailed=True
        )
        self.assertIn("Development update", updated_html)
        self.assertIn("Compare source revisions", updated_html)
        self.assertIn(
            f"https://github.com/example/example/compare/{'a' * 40}...{'b' * 40}",
            updated_html,
        )

    def test_published_release_is_selected_over_newer_development(self) -> None:
        releases = [
            {"version": "0.2.0-dev"},
            {"version": "0.1.0"},
        ]
        self.assertEqual(generate_site.select_release(releases)["version"], "0.1.0")
        self.assertTrue(generate_site.is_development_version("0.2.0-dev"))
        self.assertFalse(generate_site.is_development_version("16.1.0-patchset.1.1.0"))

    def test_package_selection_records_published_and_development_status(self) -> None:
        for package in self.catalog["packages"]:
            selected = next(
                release
                for release in package["versions"]
                if release["version"] == package["selected_version"]
            )
            development_only = all(release["development"] for release in package["versions"])
            self.assertEqual(package["development_only"], development_only)
            self.assertEqual(selected["development"], development_only)

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

    def test_version_sets_follow_alire_semantics(self) -> None:
        #  Verified against Semantic_Versioning as built by Alire; note that '~'
        #  stays within the minor series and that pre-releases are not excluded.
        expectations = [
            ("*", "16.2.0", True),
            ("any", "16.2.0", True),
            ("~0.1.0", "0.1.0", True),
            ("~0.1.0", "0.1.1-dev", True),
            ("~0.1.0", "0.2.0", False),
            ("~0.1.1-dev", "0.1.0", False),
            ("~0.1.1-dev", "0.1.1", True),
            ("^0.2.0", "0.9.0", True),
            ("^0.2.0", "1.0.0", False),
            ("^1.0.0", "0.9.9", False),
            (">=13 & <17", "13.2.0", True),
            (">=13 & <17", "16.1.0-patchset.1.1.0", True),
            (">=13 & <17", "17.0.0", False),
            ("16.1.0", "16.1.0", True),
            ("16.1.0", "16.2.0", False),
            (">1.0.0", "1.0.0", False),
            ("/=1.0.0", "1.0.0", False),
            ("^1 | ^2", "2.3.0", True),
            ("^1 | ^2", "3.0.0", False),
            ("!^1", "2.0.0", True),
            ("(>=1 & <2) | =3.0.0", "3.0.0", True),
            ("\u22651.0.0", "1.5.0", True),
            ("0.1.0-alpha.2", "0.1.0-alpha.10", False),
            (">=0.1.0-alpha.2", "0.1.0-alpha.10", True),
        ]
        for requirement, version, expected in expectations:
            with self.subTest(requirement=requirement, version=version):
                self.assertIs(
                    generate_site.requirement_admits(requirement, version), expected
                )

    def test_unreadable_requirements_fail_generation(self) -> None:
        for requirement, version in (("not a version set", "1.0.0"), ("^1.0.0", "not a version")):
            with self.subTest(requirement=requirement, version=version):
                with self.assertRaises(generate_site.VersionSyntaxError):
                    generate_site.requirement_admits(requirement, version)

        def package(name: str, manifest: dict) -> dict:
            return {
                "name": name,
                "selected_version": "1.0.0",
                "versions": [
                    {
                        "version": "1.0.0",
                        "development": False,
                        "path": f"index/xx/{name}/{name}-1.0.0.toml",
                        "manifest": manifest,
                    }
                ],
            }

        #  A requirement the generator cannot read names the dependant manifest.
        with self.assertRaises(ValueError) as raised:
            generate_site.attach_dependants(
                [
                    package("base", {}),
                    package("consumer", {"depends-on": [{"base": "wildly invalid"}]}),
                ]
            )
        message = str(raised.exception)
        self.assertIn("index/xx/consumer/consumer-1.0.0.toml", message)
        self.assertIn("wildly invalid", message)
        self.assertNotIn("index/xx/base/base-1.0.0.toml", message)

        #  An unreadable provided version names the release that provides it.
        with self.assertRaises(ValueError) as raised:
            generate_site.attach_dependants(
                [
                    package("base", {"provides": ["stand_in=not a version"]}),
                    package("consumer", {"depends-on": [{"stand_in": "^1.0.0"}]}),
                ]
            )
        message = str(raised.exception)
        self.assertIn("index/xx/base/base-1.0.0.toml", message)
        self.assertIn("not a version", message)

        #  A dynamic expression has no one crate name, so it would silently
        #  drop out of the index rather than resolve to a dependant row.
        with self.assertRaises(ValueError) as raised:
            generate_site.attach_dependants(
                [
                    package("base", {}),
                    package(
                        "consumer",
                        {"depends-on": [{"case(os)": {"linux": {"base": "^1.0.0"}}}]},
                    ),
                ]
            )
        message = str(raised.exception)
        self.assertIn("index/xx/consumer/consumer-1.0.0.toml", message)
        self.assertIn("case(os)", message)

        #  An unreadable requirement nothing depends on cannot mislead anyone.
        generate_site.attach_dependants([package("lonely", {"provides": ["ghost=not a version"]})])

    def test_dependants_mirror_every_declared_dependency(self) -> None:
        declared = {
            (package["name"], release["version"], required.lower()): requirement
            for package in self.catalog["packages"]
            for release in package["versions"]
            for required, requirement in generate_site.dependency_map(
                release["manifest"]
            ).items()
        }
        recorded = {
            (record["name"], record["version"], record["requires"]): record["requirement"]
            for package in self.catalog["packages"]
            for release in package["versions"]
            for record in release["dependants"]
        }
        #  Every recorded dependant restates a dependency the manifest declares,
        #  and every dependency on an indexed crate is recorded somewhere.
        self.assertTrue(recorded)
        for identity, requirement in recorded.items():
            self.assertEqual(declared[identity], requirement)
        indexed = {
            name
            for package in self.catalog["packages"]
            for release in package["versions"]
            for name, _version in generate_site.provided_identities(
                package["name"], release["version"], release["manifest"]
            )
        }
        self.assertEqual(
            {identity for identity in declared if identity[2] in indexed},
            set(recorded),
        )

    def test_dependants_are_grouped_by_crate_and_newest_first(self) -> None:
        for package in self.catalog["packages"]:
            for release in package["versions"]:
                names = [record["name"] for record in release["dependants"]]
                self.assertEqual(names, sorted(names))
                self.assertNotIn(package["name"], names)
                for name in set(names):
                    versions = [
                        record["version"]
                        for record in release["dependants"]
                        if record["name"] == name
                    ]
                    self.assertEqual(
                        versions,
                        sorted(versions, key=generate_site.version_key, reverse=True),
                    )
                for record in release["dependants"]:
                    selected = next(
                        candidate["selected_version"]
                        for candidate in self.catalog["packages"]
                        if candidate["name"] == record["name"]
                    )
                    self.assertEqual(record["selected"], record["version"] == selected)

    def test_dependants_resolve_through_provided_crate_identities(self) -> None:
        toolchain = next(
            package
            for package in self.catalog["packages"]
            if package["name"] == "gnat_flyology_native"
        )
        self.assertEqual(
            generate_site.provided_identities(
                "gnat_flyology_native",
                "16.2.0-patchset.1.1.0",
                {"provides": ["gnat=16.2.0"]},
            ),
            [("gnat_flyology_native", "16.2.0-patchset.1.1.0"), ("gnat", "16.2.0")],
        )
        #  flyology_simd pins gnat 16.1.0, so only the 16.1.0 patchsets qualify.
        for release in toolchain["versions"]:
            simd = next(
                record
                for record in release["dependants"]
                if record["name"] == "flyology_simd"
            )
            self.assertEqual(simd["requires"], "gnat")
            self.assertEqual(simd["requirement"], "16.1.0")
            self.assertEqual(
                simd["qualifies"], release["version"].startswith("16.1.0")
            )
            self.assertEqual(
                simd["provided_version"], release["version"].partition("-")[0]
            )

    def test_dependants_qualify_against_the_version_on_the_page(self) -> None:
        core = next(
            package
            for package in self.catalog["packages"]
            if package["name"] == "flyology_postgres_sql_core"
        )
        published = generate_site.release_for(core, "0.1.0")
        development = generate_site.release_for(core, "0.1.1-dev")
        #  A '~0.1.1-dev' dependant is excluded by 0.1.0 but admitted by 0.1.1-dev.
        for release, expected in ((published, False), (development, True)):
            record = next(
                item
                for item in release["dependants"]
                if item["name"] == "flyology_postgres_sql_v14"
                and item["version"] == "0.1.1-dev"
            )
            self.assertEqual(record["requirement"], "~0.1.1-dev")
            self.assertIs(record["qualifies"], expected)

    def test_json_records_dependants_for_every_release(self) -> None:
        aggregate = json.loads((self.output / "crates.json").read_text(encoding="utf-8"))
        fields = {
            "name",
            "version",
            "development",
            "selected",
            "path",
            "requires",
            "requirement",
            "provided_version",
            "qualifies",
        }
        for package in aggregate["packages"]:
            for release in package["versions"]:
                self.assertIn("dependants", release)
                for record in release["dependants"]:
                    self.assertEqual(set(record), fields)
                    self.assertIsInstance(record["qualifies"], bool)

    def test_pages_render_dependants_with_the_selected_version_in_bold(self) -> None:
        home = (self.output / "index.html").read_text(encoding="utf-8")
        crate = (self.output / "crates" / "flyology" / "index.html").read_text(encoding="utf-8")
        version = (
            self.output / "crates" / "gnat_flyology_native" / "16.2.0-patchset.1.1.0" / "index.html"
        ).read_text(encoding="utf-8")

        self.assertEqual(home.count(">Dependants</h4>"), len(self.catalog["packages"]))
        self.assertIn(
            'Resolved against <code>flyology 0.1.0</code> — 3 of 6 dependant releases qualify.',
            crate,
        )
        self.assertIn(
            '<a href="../../crates/flyology_http/0.1.0/"><strong>0.1.0</strong>'
            '<span class="visually-hidden"> (selected version)</span></a>'
            '<code>~0.1.0</code><span class="dependant-verdict">Qualifies</span>',
            crate,
        )
        self.assertIn(
            '<a href="../../crates/flyology_http/0.1.1-dev/">0.1.1-dev</a>'
            '<code>~0.1.1-dev</code><span class="dependant-verdict">Excluded</span>',
            crate,
        )
        #  A provided identity names the crate it stands in for.
        self.assertIn('Resolved against <code>gnat 16.2.0</code>', version)
        self.assertIn('<code>gnat 16.1.0</code><span class="dependant-verdict">Excluded</span>', version)
        self.assertIn('<code>gnat &gt;=13 &amp; &lt;17</code>', version)

    def test_crates_without_dependants_say_so(self) -> None:
        page = (self.output / "crates" / "flyology_http" / "index.html").read_text(encoding="utf-8")
        package = next(
            candidate
            for candidate in self.catalog["packages"]
            if candidate["name"] == "flyology_http"
        )
        selected = generate_site.release_for(package, package["selected_version"])
        self.assertEqual(selected["dependants"], [])
        self.assertIn("No indexed release depends on this version.", page)

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
