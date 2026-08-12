#!/usr/bin/env python3
"""Generate the Flyology Alire index website and JSON catalog."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import tomllib
from collections import defaultdict
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_URL = "https://crates.flyology.org/"
SCHEMA_VERSION = 1


def json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value


def generated_at() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    instant = datetime.fromtimestamp(int(epoch), UTC) if epoch else datetime.now(UTC)
    return instant.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def version_key(version: str) -> tuple[Any, ...]:
    main, separator, prerelease = version.partition("-")
    numbers = tuple(int(part) if part.isdigit() else part for part in main.split("."))
    prerelease_parts = tuple(
        int(part) if part.isdigit() else part
        for part in re.split(r"[.-]", prerelease)
        if part
    )
    return numbers, not separator, prerelease_parts


def load_catalog(source: Path) -> dict[str, Any]:
    index_path = source / "index.toml"
    with index_path.open("rb") as stream:
        index_metadata = json_value(tomllib.load(stream))

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for manifest_path in sorted(source.glob("*/*/*.toml")):
        with manifest_path.open("rb") as stream:
            manifest = json_value(tomllib.load(stream))
        try:
            identity = manifest["name"], manifest["version"]
        except KeyError as error:
            raise ValueError(f"{manifest_path} is missing {error.args[0]!r}") from error
        if identity in seen:
            raise ValueError(f"duplicate manifest for {identity[0]} {identity[1]}")
        seen.add(identity)
        grouped[identity[0]].append(
            {
                "version": identity[1],
                "path": manifest_path.relative_to(ROOT).as_posix(),
                "manifest": manifest,
            }
        )

    packages = []
    for name, releases in sorted(grouped.items()):
        releases.sort(key=lambda release: version_key(release["version"]), reverse=True)
        newest = releases[0]["manifest"]
        packages.append(
            {
                "name": name,
                "description": newest.get("description", "No description provided."),
                "latest_version": releases[0]["version"],
                "versions": releases,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at(),
        "canonical_url": CANONICAL_URL,
        "index": index_metadata,
        "packages": packages,
    }


def text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(text(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def field(label: str, value: Any, *, link: bool = False) -> str:
    if value is None or value == [] or value == {}:
        return ""
    escaped = html.escape(text(value))
    rendered = f'<a href="{html.escape(str(value), quote=True)}">{escaped}</a>' if link else escaped
    return f"<div><dt>{html.escape(label)}</dt><dd>{rendered}</dd></div>"


def dependency_rows(manifest: dict[str, Any]) -> str:
    dependencies = manifest.get("depends-on", [])
    if not dependencies:
        return '<p class="quiet">No package dependencies declared.</p>'
    rows = []
    for group in dependencies:
        for name, constraint in group.items():
            rows.append(
                f'<li><code>{html.escape(name)}</code><span>{html.escape(text(constraint))}</span></li>'
            )
    return '<ul class="dependency-list">' + "".join(rows) + "</ul>"


def origin_summary(manifest: dict[str, Any]) -> str:
    origin = manifest.get("origin")
    if not origin:
        return "Not declared"
    if isinstance(origin, dict) and "url" in origin:
        commit = f" @ {origin['commit'][:12]}" if origin.get("commit") else ""
        return f"{origin['url']}{commit}"
    return "Platform-specific binary archives"


def package_kind(manifest: dict[str, Any]) -> str:
    return "toolchain" if manifest.get("provides") or manifest.get("auto-gpr-with") is False else "source"


def render_version(package_name: str, release: dict[str, Any], open_first: bool) -> str:
    manifest = release["manifest"]
    raw = html.escape(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    metadata = "".join(
        [
            field("Authors", manifest.get("authors")),
            field("Maintainers", manifest.get("maintainers")),
            field("Licenses", manifest.get("licenses")),
            field("Website", manifest.get("website"), link=True),
            field("Provides", manifest.get("provides")),
            field("Project files", manifest.get("project-files")),
            field("Origin", origin_summary(manifest)),
            field("Manifest", release["path"]),
        ]
    )
    return f"""
      <details class="version"{' open' if open_first else ''}>
        <summary>
          <span>{html.escape(release['version'])}</span>
          <span>{html.escape(release['path'])}</span>
        </summary>
        <div class="version-body">
          <dl class="metadata">{metadata}</dl>
          <section class="detail-section" aria-labelledby="deps-{html.escape(package_name)}-{html.escape(release['version'])}">
            <h4 id="deps-{html.escape(package_name)}-{html.escape(release['version'])}">Dependencies</h4>
            {dependency_rows(manifest)}
          </section>
          <details class="raw-manifest">
            <summary>Complete manifest as JSON</summary>
            <pre><code>{raw}</code></pre>
          </details>
        </div>
      </details>"""


def render_package(package: dict[str, Any]) -> str:
    newest = package["versions"][0]["manifest"]
    tags = newest.get("tags", [])
    kind = package_kind(newest)
    search = " ".join(
        [package["name"], package["description"], package["latest_version"], *tags]
    ).lower()
    tag_html = "".join(f"<li>{html.escape(tag)}</li>" for tag in tags)
    versions = "".join(
        render_version(package["name"], release, index == 0)
        for index, release in enumerate(package["versions"])
    )
    return f"""
    <details class="package" data-package data-kind="{kind}" data-search="{html.escape(search, quote=True)}">
      <summary class="package-summary">
        <span class="package-identity">
          <span class="package-name">{html.escape(package['name'])}</span>
          <span class="version-badge">{html.escape(package['latest_version'])}</span>
        </span>
        <span class="package-description">{html.escape(package['description'])}</span>
        <span class="summary-action" aria-hidden="true">Details <span>+</span></span>
      </summary>
      <div class="package-body">
        <div class="package-facts">
          <span class="kind-label">{kind.capitalize()}</span>
          <ul class="tag-list" aria-label="Tags">{tag_html}</ul>
          <a href="crates/{html.escape(package['name'])}.json" download>Download package JSON</a>
        </div>
        <div class="versions">
          <h3>{len(package['versions'])} indexed version{'s' if len(package['versions']) != 1 else ''}</h3>
          {versions}
        </div>
      </div>
    </details>"""


def render_html(catalog: dict[str, Any]) -> str:
    packages = catalog["packages"]
    manifest_count = sum(len(package["versions"]) for package in packages)
    source_count = sum(package_kind(package["versions"][0]["manifest"]) == "source" for package in packages)
    package_html = "".join(render_package(package) for package in packages)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="Packages and compiler builds published in the Flyology Alire index.">
    <meta name="theme-color" content="#17213d">
    <title>Flyology Crate Index</title>
    <link rel="canonical" href="{CANONICAL_URL}">
    <link rel="icon" href="favicon.svg" type="image/svg+xml">
    <link rel="stylesheet" href="assets/styles/site.css">
    <link rel="stylesheet" href="assets/styles/index.css">
    <script src="assets/scripts/ada-highlight.js"></script>
    <script src="assets/scripts/site.js"></script>
    <script src="assets/scripts/index.js" defer></script>
  </head>
  <body>
    <a class="skip-link" href="#catalog">Skip to crate catalog</a>
    <header class="site-header">
      <nav class="site-nav" aria-label="Primary navigation">
        <a class="brand" href="./" aria-label="Flyology Crate Index home">
          <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><path d="M4 17c6-1 9-5 12-12 3 7 6 11 12 12-6 1-9 4-12 10-3-6-6-9-12-10Z" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="16" cy="17" r="3" fill="currentColor"/></svg>
          <span>Flyology Crates</span>
        </a>
        <ul class="nav-links" data-nav-links>
          <li><a href="#catalog" aria-current="page">Packages</a></li>
          <li><a href="crates.json" download>JSON</a></li>
          <li><a href="https://github.com/flyology-ada/alire-index">GitHub</a></li>
        </ul>
        <div class="nav-tools">
          <button class="icon-button" type="button" data-theme-toggle>
            <span class="visually-hidden" data-theme-label>Use dark theme</span>
            <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"/><circle cx="12" cy="12" r="4"/></svg>
          </button>
          <button class="menu-button" type="button" data-menu-toggle aria-expanded="false">
            <span class="visually-hidden">Toggle navigation</span>
            <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
          </button>
        </div>
      </nav>
    </header>
    <main>
      <section class="catalog-hero page-shell" aria-labelledby="page-title">
        <div>
          <p class="eyebrow">Flyology packages</p>
          <h1 id="page-title">Flyology Alire index.</h1>
          <p class="hero-lede">Packages and compiler builds maintained by Flyology. This page and its JSON files are generated directly from the index manifests.</p>
          <div class="actions">
            <a class="button button-primary" href="#catalog">View {len(packages)} packages</a>
            <a class="button button-secondary" href="crates.json" download>Download JSON</a>
          </div>
        </div>
        <div class="install-panel">
          <div class="install-heading"><span>Configure Alire</span><span>shell</span></div>
          <pre><code id="install-command">alr index \\
<span class="install-option">  --add=</span>git+https://github.com/flyology-ada/alire-index.git \\
<span class="install-option">  --name=</span>flyology \\
<span class="install-option">  --before=</span>community</code></pre>
          <button type="button" data-copy-install>Copy command</button>
        </div>
      </section>
      <div class="catalog-stats" aria-label="Index summary">
        <div class="page-shell">
          <p><strong>{len(packages)}</strong> packages</p>
          <p><strong>{manifest_count}</strong> manifests</p>
          <p><strong>{source_count}</strong> source crates</p>
          <p><strong>v{html.escape(str(catalog['index'].get('version', 'unknown')))}</strong> index format</p>
        </div>
      </div>
      <section class="catalog page-shell" id="catalog" aria-labelledby="catalog-title" data-catalog>
        <div class="catalog-heading">
          <div>
            <p class="eyebrow">Package list</p>
            <h2 id="catalog-title">Indexed packages.</h2>
          </div>
          <p>Search by name, description, version, or tag. Each package includes its dependencies, origin, availability rules, and complete parsed manifest.</p>
        </div>
        <div class="catalog-controls">
          <label class="search-control">
            <span>Search packages</span>
            <input type="search" placeholder="Try http, postgres, compiler…" data-search>
          </label>
          <label class="type-control">
            <span>Package type</span>
            <select data-kind-filter>
              <option value="all">All packages</option>
              <option value="source">Source crates</option>
              <option value="toolchain">Toolchains</option>
            </select>
          </label>
          <button class="expand-button" type="button" data-expand-all>Expand visible</button>
        </div>
        <p class="result-count" aria-live="polite" data-result-count>Showing all {len(packages)} packages</p>
        <div class="package-list">{package_html}</div>
        <div class="empty-state" data-empty hidden>
          <h3>No packages match</h3>
          <p>Use a shorter search term or select all package types.</p>
        </div>
      </section>
    </main>
    <footer class="site-footer">
      <div class="footer-inner">
        <p>Generated from the <a href="https://github.com/flyology-ada/alire-index">Flyology Alire index</a>.</p>
        <div class="footer-links"><a href="crates.json">Aggregate JSON</a><a href="README.txt">JSON schema notes</a></div>
      </div>
    </footer>
  </body>
</html>
"""


INDEX_CSS = r"""
.brand-mark { width: 2rem; height: 2rem; color: var(--violet-deep); }
.catalog-hero { display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(21rem, .72fr); align-items: center; min-height: min(48rem, calc(100svh - 4.75rem)); padding-block: clamp(5rem, 10vw, 8.5rem); gap: clamp(3rem, 8vw, 8rem); }
.catalog-hero h1 { max-width: 10ch; font-size: clamp(3.2rem, 7vw, 7rem); }
.install-panel { overflow: hidden; border: 1px solid var(--code-line); border-radius: var(--radius-md); background: var(--code-bg); color: oklch(91% .02 270); box-shadow: 0 1.7rem 4rem oklch(10% .04 270 / .22); }
.install-heading { display: flex; justify-content: space-between; padding: .8rem 1.1rem; border-bottom: 1px solid var(--code-line); color: oklch(72% .03 270); font: .72rem var(--font-mono); }
.install-panel pre { margin: 0; padding: 1.4rem 1.1rem; overflow-x: hidden; font-size: .78rem; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; }
.install-option { white-space: nowrap; }
.install-panel button { width: 100%; padding: .8rem 1.1rem; border: 0; border-top: 1px solid var(--code-line); background: oklch(28% .052 270); color: oklch(92% .02 270); font: 600 .76rem var(--font-sans); text-align: left; cursor: pointer; }
.install-panel button:hover { background: oklch(33% .06 270); }
.catalog-stats { border-block: 1px solid var(--line); background: var(--surface); }
.catalog-stats .page-shell { display: grid; grid-template-columns: repeat(4, 1fr); }
.catalog-stats p { margin: 0; padding: 1.2rem clamp(.8rem, 2.5vw, 1.8rem); border-right: 1px solid var(--line); color: var(--ink-soft); font-size: .78rem; }
.catalog-stats p:first-child { padding-left: 0; }
.catalog-stats p:last-child { border-right: 0; }
.catalog-stats strong { display: block; color: var(--ink); font: 620 1rem var(--font-mono); }
.catalog { padding-block: clamp(5.5rem, 10vw, 9rem); }
.catalog-heading { display: grid; grid-template-columns: minmax(0, .9fr) minmax(18rem, .55fr); align-items: end; gap: 3rem; margin-bottom: 3rem; }
.catalog-heading h2 { max-width: 12ch; margin-bottom: 0; }
.catalog-heading > p { max-width: 54ch; margin-bottom: .3rem; color: var(--ink-soft); }
.catalog-controls { display: grid; grid-template-columns: minmax(15rem, 1fr) minmax(11rem, .32fr) auto; align-items: end; gap: .85rem; padding-block: 1.2rem; border-block: 1px solid var(--line); }
.catalog-controls label { display: grid; gap: .35rem; color: var(--ink-soft); font-size: .72rem; font-weight: 620; }
.catalog-controls input, .catalog-controls select, .expand-button { min-height: 3rem; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--surface); color: var(--ink); font: inherit; }
.catalog-controls input, .catalog-controls select { width: 100%; padding: .7rem .9rem; font-size: .9rem; }
.catalog-controls input:focus, .catalog-controls select:focus { border-color: var(--violet); }
.expand-button { padding: .7rem 1rem; font-size: .82rem; font-weight: 620; cursor: pointer; }
.expand-button:hover { background: var(--surface-strong); }
.result-count { margin: 1rem 0; color: var(--ink-soft); font: .74rem var(--font-mono); }
.package-list { border-top: 1px solid var(--line); }
.package { border-bottom: 1px solid var(--line); }
.package[hidden] { display: none; }
.package-summary { display: grid; grid-template-columns: minmax(13rem, .72fr) minmax(16rem, 1.2fr) auto; align-items: center; min-height: 7.2rem; padding: 1.2rem .25rem; gap: 1.4rem; cursor: pointer; list-style: none; transition: background-color 180ms var(--ease-out), padding 180ms var(--ease-out); }
.package-summary::-webkit-details-marker, .version > summary::-webkit-details-marker, .raw-manifest > summary::-webkit-details-marker { display: none; }
.package-summary:hover, .package[open] > .package-summary { padding-inline: 1rem; background: var(--surface); }
.package-identity { display: flex; flex-wrap: wrap; align-items: baseline; gap: .65rem; min-width: 0; }
.package-name { overflow-wrap: anywhere; font-size: clamp(1.05rem, 2vw, 1.4rem); font-weight: 620; letter-spacing: -.025em; }
.version-badge { color: var(--violet-deep); font: .72rem var(--font-mono); }
.package-description { max-width: 58ch; color: var(--ink-soft); font-size: .92rem; }
.summary-action { display: inline-flex; align-items: center; gap: .6rem; color: var(--ink-soft); font-size: .72rem; }
.summary-action span { display: grid; width: 2rem; height: 2rem; place-items: center; border: 1px solid var(--line); border-radius: 50%; color: var(--ink); font-size: 1rem; transition: transform 200ms var(--ease-out); }
.package[open] > summary .summary-action span { transform: rotate(45deg); }
.package-body { padding: 0 1rem 2rem; }
.package-facts { display: flex; flex-wrap: wrap; align-items: center; gap: .65rem 1rem; padding: 1rem 0 1.5rem; border-bottom: 1px solid var(--line); }
.kind-label { color: var(--teal-deep); font: 650 .7rem var(--font-mono); text-transform: uppercase; letter-spacing: .06em; }
.tag-list { display: flex; flex-wrap: wrap; margin: 0; padding: 0; gap: .35rem; list-style: none; }
.tag-list li { padding: .16rem .48rem; border: 1px solid var(--line); border-radius: 999px; color: var(--ink-soft); font-size: .68rem; }
.package-facts > a { margin-left: auto; font-size: .76rem; font-weight: 580; }
.versions h3 { margin: 1.8rem 0 1rem; font-size: 1rem; letter-spacing: -.015em; }
.version { border-top: 1px solid var(--line); }
.version:last-child { border-bottom: 1px solid var(--line); }
.version > summary { display: grid; grid-template-columns: minmax(10rem, .35fr) minmax(15rem, 1fr); padding: .9rem .2rem; gap: 1rem; cursor: pointer; list-style: none; font: .76rem var(--font-mono); }
.version > summary span:last-child { overflow-wrap: anywhere; color: var(--ink-soft); }
.version[open] > summary { color: var(--violet-deep); }
.version-body { padding: .4rem 0 1.6rem; }
.metadata { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; border-top: 1px solid var(--line); }
.metadata div { display: grid; grid-template-columns: 8rem 1fr; padding: .75rem .2rem; gap: 1rem; border-bottom: 1px solid var(--line); }
.metadata div:nth-child(odd) { margin-right: 1.2rem; }
.metadata dt { color: var(--ink-soft); font-size: .7rem; }
.metadata dd { min-width: 0; margin: 0; overflow-wrap: anywhere; font: .72rem/1.55 var(--font-mono); }
.detail-section { margin-top: 1.6rem; }
.detail-section h4 { margin-bottom: .7rem; font-size: .82rem; }
.quiet { color: var(--ink-soft); font-size: .8rem; }
.dependency-list { display: grid; margin: 0; padding: 0; gap: .45rem; list-style: none; }
.dependency-list li { display: grid; grid-template-columns: minmax(10rem, .3fr) 1fr; padding: .55rem .7rem; gap: 1rem; background: var(--surface); font-size: .76rem; }
.dependency-list span { color: var(--ink-soft); font-family: var(--font-mono); }
.raw-manifest { margin-top: 1.5rem; }
.raw-manifest > summary { display: inline-flex; padding: .6rem .8rem; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--surface); font-size: .76rem; font-weight: 600; cursor: pointer; list-style: none; }
.raw-manifest pre { max-height: 32rem; margin: .7rem 0 0; padding: 1rem; overflow: auto; border: 1px solid var(--code-line); border-radius: var(--radius-md); background: var(--code-bg); color: oklch(91% .02 270); font-size: .72rem; line-height: 1.65; }
.empty-state { padding: 4rem 1rem; border-bottom: 1px solid var(--line); text-align: center; }
.empty-state h3 { margin-bottom: .5rem; }
.empty-state p { color: var(--ink-soft); }
@media (max-width: 900px) {
  .catalog-hero { grid-template-columns: 1fr; min-height: 0; }
  .install-panel { max-width: 38rem; }
  .catalog-heading { grid-template-columns: 1fr; gap: 1.2rem; }
  .catalog-controls { grid-template-columns: 1fr 1fr; }
  .expand-button { grid-column: 1 / -1; }
  .package-summary { grid-template-columns: 1fr auto; }
  .package-description { grid-column: 1 / -1; grid-row: 2; }
  .summary-action { grid-column: 2; grid-row: 1; }
  .metadata { grid-template-columns: 1fr; }
  .metadata div:nth-child(odd) { margin-right: 0; }
}
@media (max-width: 640px) {
  .catalog-stats .page-shell { grid-template-columns: repeat(2, 1fr); }
  .catalog-stats p:nth-child(2) { border-right: 0; }
  .catalog-stats p:nth-child(-n+2) { border-bottom: 1px solid var(--line); }
  .catalog-stats p:nth-child(3) { padding-left: 0; }
  .catalog-controls { grid-template-columns: 1fr; }
  .expand-button { grid-column: auto; }
  .package-summary { min-height: 0; padding-block: 1.4rem; }
  .package-body { padding-inline: .35rem; }
  .package-facts > a { width: 100%; margin-left: 0; }
  .version > summary, .metadata div, .dependency-list li { grid-template-columns: 1fr; gap: .3rem; }
}
@media (prefers-reduced-motion: reduce) {
  .package-summary, .summary-action span { transition: none; }
}
"""


INDEX_JS = r"""
(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    const catalog = document.querySelector("[data-catalog]");
    if (!catalog) return;
    const search = catalog.querySelector("[data-search]");
    const kind = catalog.querySelector("[data-kind-filter]");
    const count = catalog.querySelector("[data-result-count]");
    const empty = catalog.querySelector("[data-empty]");
    const expand = catalog.querySelector("[data-expand-all]");
    const packages = Array.from(catalog.querySelectorAll("[data-package]"));

    function visiblePackages() {
      return packages.filter(function (item) { return !item.hidden; });
    }

    function filter() {
      const query = search.value.trim().toLowerCase();
      packages.forEach(function (item) {
        const matchesText = !query || item.dataset.search.includes(query);
        const matchesKind = kind.value === "all" || item.dataset.kind === kind.value;
        item.hidden = !(matchesText && matchesKind);
      });
      const visible = visiblePackages().length;
      count.textContent = visible === packages.length
        ? "Showing all " + packages.length + " packages"
        : "Showing " + visible + " of " + packages.length + " packages";
      empty.hidden = visible !== 0;
      expand.textContent = "Expand visible";
      expand.dataset.expanded = "false";
    }

    search.addEventListener("input", filter);
    kind.addEventListener("change", filter);
    expand.addEventListener("click", function () {
      const shouldOpen = expand.dataset.expanded !== "true";
      visiblePackages().forEach(function (item) { item.open = shouldOpen; });
      expand.dataset.expanded = String(shouldOpen);
      expand.textContent = shouldOpen ? "Collapse visible" : "Expand visible";
    });

    const copy = document.querySelector("[data-copy-install]");
    const command = document.querySelector("#install-command");
    if (copy && command) {
      copy.addEventListener("click", async function () {
        const previous = copy.textContent;
        try {
          await navigator.clipboard.writeText(command.textContent.trim());
          copy.textContent = "Copied";
        } catch (_error) {
          copy.textContent = "Select the command above";
        }
        window.setTimeout(function () { copy.textContent = previous; }, 1600);
      });
    }
    filter();
  });
})();
"""


README_TEXT = """Flyology crate index JSON
==========================

crates.json is the aggregate catalog. Its schema_version is currently 1.
Each crates/<name>.json file contains the same package object plus the catalog
schema_version, generated_at, canonical_url, and index metadata.

Every version has a path and manifest field. The manifest is a lossless JSON
representation of the corresponding TOML manifest, subject only to TOML date
and time values being represented as ISO 8601 strings.

Browser clients on other origins can fetch these files because GitHub Pages
serves static assets with Access-Control-Allow-Origin: *.
"""


FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="#f7f7fc"/>
  <path d="M4 17c6-1 9-5 12-12 3 7 6 11 12 12-6 1-9 4-12 10-3-6-6-9-12-10Z" fill="none" stroke="#674fc2" stroke-width="2"/>
  <circle cx="16" cy="17" r="3" fill="#674fc2"/>
</svg>
"""


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def generate(source: Path, output: Path) -> dict[str, Any]:
    catalog = load_catalog(source)
    if output.exists():
        shutil.rmtree(output)
    (output / "assets" / "styles").mkdir(parents=True)
    (output / "assets" / "scripts").mkdir(parents=True)
    (output / "crates").mkdir(parents=True)

    write_json(output / "crates.json", catalog)
    shared = {key: catalog[key] for key in ("schema_version", "generated_at", "canonical_url", "index")}
    for package in catalog["packages"]:
        write_json(output / "crates" / f"{package['name']}.json", {**shared, "package": package})

    (output / "index.html").write_text(render_html(catalog), encoding="utf-8")
    (output / "assets" / "styles" / "index.css").write_text(INDEX_CSS.strip() + "\n", encoding="utf-8")
    (output / "assets" / "scripts" / "index.js").write_text(INDEX_JS.strip() + "\n", encoding="utf-8")
    (output / "favicon.svg").write_text(FAVICON, encoding="utf-8")
    (output / "README.txt").write_text(README_TEXT, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "index")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "site")
    args = parser.parse_args()
    try:
        catalog = generate(args.source.resolve(), args.output.resolve())
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        print(f"site generation failed: {error}", file=sys.stderr)
        return 1
    versions = sum(len(package["versions"]) for package in catalog["packages"])
    print(f"Generated {len(catalog['packages'])} packages and {versions} manifests at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
