#!/usr/bin/env python3
"""Import immutable gnat_flyology_native manifests from gnat-patches releases."""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tomllib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RELEASES = os.environ.get(
    "GNAT_PATCHES_RELEASES_API",
    "https://api.github.com/repos/flyology-ada/gnat-patches/releases?per_page=100",
)
TAG = re.compile(
    r"patchset-(?P<patchset>\d+\.\d+\.\d+)-gcc-"
    r"(?P<compiler>\d+(?:\.\d+\.\d+)?)"
)
VERSION = re.compile(
    r"(?P<compiler>\d+\.\d+\.\d+)-patchset\."
    r"(?P<patchset>\d+\.\d+\.\d+)"
)
PLATFORMS = {"linux-x86_64", "linux-aarch64", "macos-aarch64"}


class ImportError(RuntimeError):
    pass


def tag_matches_version(version: str, tag: str) -> bool:
    version_match = VERSION.fullmatch(version)
    tag_match = TAG.fullmatch(tag)
    if not version_match or not tag_match:
        return False
    compiler = version_match.group("compiler")
    tagged_compiler = tag_match.group("compiler")
    return (
        version_match.group("patchset") == tag_match.group("patchset")
        and tagged_compiler in {compiler, compiler.partition(".")[0]}
    )


def fetch(url: str) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "flyology-alire-index-importer",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as response:
        return response.read()


def binary_origins(value) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, dict):
        if value.get("binary") is True:
            found.append(value)
        for child in value.values():
            found.extend(binary_origins(child))
    return found


def validate_manifest(content: bytes, name: str, tag: str, assets: dict[str, dict]) -> pathlib.Path:
    try:
        data = tomllib.loads(content.decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ImportError(f"invalid TOML in {name}: {exc}") from exc
    if data.get("name") != "gnat_flyology_native":
        raise ImportError(f"unexpected crate name in {name}")
    version = data.get("version", "")
    version_match = VERSION.fullmatch(version)
    if not version_match or not tag_matches_version(version, tag):
        raise ImportError(f"version/tag mismatch for {name}")
    if name != f"gnat_flyology_native-{version}.toml":
        raise ImportError(f"manifest filename/version mismatch: {name}")
    if data.get("provides") != [f"gnat={version_match.group('compiler')}"]:
        raise ImportError(f"invalid GNAT provider declaration in {name}")

    origins = binary_origins(data.get("origin", {}))
    if len(origins) != 3:
        raise ImportError(f"{name} must contain exactly three binary origins")
    platforms: set[str] = set()
    for origin in origins:
        url = origin.get("url", "")
        hashes = origin.get("hashes", [])
        if len(hashes) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", hashes[0]):
            raise ImportError(f"invalid origin checksum in {name}")
        archive = url.rsplit("/", 1)[-1]
        expected_prefix = (
            f"https://github.com/flyology-ada/gnat-patches/releases/download/{tag}/"
        )
        if url != expected_prefix + archive or archive not in assets:
            raise ImportError(f"origin is not an asset of {tag}: {url}")
        platform_match = re.search(r"-(linux-x86_64|linux-aarch64|macos-aarch64)\.tar\.gz$", archive)
        if not platform_match:
            raise ImportError(f"unsupported toolchain platform in {archive}")
        platforms.add(platform_match.group(1))
        sidecar_name = archive + ".sha256"
        sidecar = assets.get(sidecar_name)
        if not sidecar:
            raise ImportError(f"missing checksum sidecar for {archive}")
        expected_sidecar = f"{hashes[0].removeprefix('sha256:')}  {archive}\n".encode()
        if fetch(sidecar["browser_download_url"]) != expected_sidecar:
            raise ImportError(f"release checksum sidecar differs for {archive}")
    if platforms != PLATFORMS:
        raise ImportError(f"incomplete platform set in {name}: {sorted(platforms)}")
    return ROOT / "index" / "gn" / "gnat_flyology_native" / name


def main() -> int:
    try:
        releases = json.loads(fetch(RELEASES))
        imported = 0
        for release in releases:
            tag = release.get("tag_name", "")
            if release.get("draft") or release.get("prerelease") or not TAG.fullmatch(tag):
                continue
            assets = {asset["name"]: asset for asset in release.get("assets", [])}
            manifests = [
                asset
                for asset in assets.values()
                if re.fullmatch(r"gnat_flyology_native-.+\.toml", asset["name"])
            ]
            if len(manifests) != 1:
                raise ImportError(f"{tag} must contain exactly one gnat_flyology_native manifest")
            manifest = manifests[0]
            content = fetch(manifest["browser_download_url"])
            destination = validate_manifest(content, manifest["name"], tag, assets)
            if destination.exists():
                if destination.read_bytes() != content:
                    raise ImportError(f"published manifest changed: {destination.relative_to(ROOT)}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            print(f"imported {destination.relative_to(ROOT)}")
            imported += 1
        print(f"GNAT toolchain imports: {imported}")
        return 0
    except (ImportError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
