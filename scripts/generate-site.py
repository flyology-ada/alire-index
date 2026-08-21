#!/usr/bin/env python3
"""Generate the Flyology Alire index website and JSON catalog."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import defaultdict
from datetime import UTC, date, datetime, time
from itertools import groupby
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple
from urllib.parse import quote, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_URL = "https://crates.flyology.org/"
SCHEMA_VERSION = 4
CHANGE_HISTORY_LIMIT = 200
HOME_CHANGE_LIMIT = 6
REPOSITORY_URL = "https://github.com/flyology-ada/alire-index"
COMMUNITY_REPOSITORY_URL = "https://github.com/alire-project/alire-index"
COMMUNITY_CANONICAL_URL = f"{CANONICAL_URL}community/"


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


def is_development_version(version: str) -> bool:
    """Return whether VERSION carries Alire's development prerelease label."""
    _main, separator, prerelease = version.partition("-")
    if not separator:
        return False
    return "dev" in re.split(r"[.-]", prerelease.lower())


class VersionSyntaxError(ValueError):
    """Raised when a version or a dependency version set cannot be parsed."""


class Semver(NamedTuple):
    """A parsed semantic version. Build metadata never affects ordering."""

    major: int
    minor: int
    patch: int
    pre_release: str
    build: str


DIGITS = "0123456789"

#  Operators recognised by Semantic_Versioning.Basic, longest match first.
RELATIONAL_OPERATORS = (
    ("/=", "/="),
    ("\u2260", "/="),
    (">=", ">="),
    ("\u2265", ">="),
    ("<=", "<="),
    ("\u2264", "<="),
    (">", ">"),
    ("<", "<"),
)


def parse_semver(description: str, *, relaxed: bool = False) -> Semver:
    """Parse DESCRIPTION the way Alire's semantic_versioning parser does.

    Relaxed parsing stores whatever it cannot read as build metadata, which is
    how Alire reads the version of an indexed release. Dependency version sets
    are parsed strictly, so a malformed constraint is reported rather than
    silently reinterpreted.
    """
    points = [0, 0, 0]
    pre_release = ""
    build = ""
    position = 0
    seen = 0

    def eat_number() -> int:
        nonlocal position
        if position >= len(description) or description[position] not in DIGITS:
            raise VersionSyntaxError(f"expected a version number in {description!r}")
        last = position
        while last < len(description) and description[last] in DIGITS:
            last += 1
        number = int(description[position:last])
        position = last
        return number

    def accept_build() -> None:
        nonlocal build, position
        build = description[position:]
        position = len(description)

    def accept_pre_release() -> None:
        nonlocal pre_release, position
        if position >= len(description):
            raise VersionSyntaxError(f"empty pre-release part in {description!r}")
        last = position + 1
        while last < len(description) and description[last] != "+":
            last += 1
        pre_release = description[position:last]
        position = last
        if position < len(description):
            position += 1
            accept_build()

    if position >= len(description) or description[position] not in DIGITS:
        raise VersionSyntaxError(f"expected a major number in {description!r}")
    while True:
        try:
            points[seen] = eat_number()
        except VersionSyntaxError:
            if seen == 0 or not relaxed:
                raise
            position -= 1
            accept_build()
        seen += 1
        if position >= len(description):
            break
        separator = description[position]
        if separator == "." and seen < 3:
            position += 1
            continue
        if separator == ".":
            if not relaxed:
                raise VersionSyntaxError(f"too many points in {description!r}")
            position += 1
            accept_build()
        elif separator == "-":
            position += 1
            accept_pre_release()
        elif separator == "+":
            position += 1
            accept_build()
        elif relaxed:
            accept_build()
        else:
            raise VersionSyntaxError(f"invalid separator in {description!r}")
        break
    return Semver(points[0], points[1], points[2], pre_release, build)


def pre_release_precedes(left: str, right: str) -> bool:
    """Order pre-release labels; a pre-release precedes its plain release."""
    if bool(left) != bool(right):
        return bool(left)
    left_parts = [part for part in left.split(".") if part]
    right_parts = [part for part in right.split(".") if part]
    for index in range(max(len(left_parts), len(right_parts))):
        if index >= len(right_parts):
            return False
        if index >= len(left_parts):
            return True
        left_part, right_part = left_parts[index], right_parts[index]
        try:
            left_number, right_number = int(left_part), int(right_part)
        except ValueError:
            if left_part != right_part:
                return left_part < right_part
            continue
        if left_number != right_number:
            return left_number < right_number
    return False


def semver_precedes(left: Semver, right: Semver) -> bool:
    left_points = (left.major, left.minor, left.patch)
    right_points = (right.major, right.minor, right.patch)
    if left_points != right_points:
        return left_points < right_points
    return pre_release_precedes(left.pre_release, right.pre_release)


def semver_equivalent(left: Semver, right: Semver) -> bool:
    return (left.major, left.minor, left.patch, left.pre_release) == (
        right.major,
        right.minor,
        right.patch,
        right.pre_release,
    )


def parse_basic_set(expression: str, *, relaxed: bool = False) -> tuple[tuple[str, Semver], ...]:
    """Parse one '&'-free restriction, e.g. '^1.2.0', '>=13' or '*'."""
    if expression.lower() == "any" or expression == "*":
        return ()
    if not expression:
        raise VersionSyntaxError("empty version set")
    if expression[0] in DIGITS:
        return (("=", parse_semver(expression, relaxed=relaxed)),)
    if expression[0] in "=^~":
        return ((expression[0], parse_semver(expression[1:], relaxed=relaxed)),)
    for prefix, operator in RELATIONAL_OPERATORS:
        if not expression.startswith(prefix):
            continue
        bound = parse_semver(expression[len(prefix):], relaxed=relaxed)
        if operator == ">":
            return ((">=", bound), ("/=", bound))
        if operator == "<":
            return (("<=", bound), ("/=", bound))
        return ((operator, bound),)
    raise VersionSyntaxError(f"invalid version set: {expression!r}")


def parse_version_set(expression: str, *, relaxed: bool = False) -> tuple[Any, ...]:
    """Parse an Alire dependency version set, including '&', '|', '!' and groups."""
    if expression.lower() == "any" or expression == "*":
        return ("set", ())
    position = 0
    length = len(expression)

    def next_token() -> str:
        nonlocal position
        while position < length and expression[position] == " ":
            position += 1
        if position >= length:
            return "end"
        character = expression[position]
        if character in "<>=/~^" or character in "\u2260\u2265\u2264":
            return "set"
        return {"&": "and", "(": "open", ")": "close", "|": "or", "!": "not"}.get(
            character, "set" if character in DIGITS else "unknown"
        )

    def match(character: str) -> None:
        nonlocal position
        if position >= length or expression[position] != character:
            raise VersionSyntaxError(f"expected {character!r} in {expression!r}")
        position += 1

    def next_basic_set() -> str:
        nonlocal position
        last = position
        while last < length and expression[last] not in "&|() ":
            last += 1
        if last == position:
            raise VersionSyntaxError(f"empty version set in {expression!r}")
        restriction = expression[position:last]
        position = last
        return restriction

    def parse_operand(kind: str, *, with_list: bool) -> tuple[Any, ...]:
        token = next_token()
        if token == "not":
            match("!")
            child = parse_operand(kind, with_list=False)
            if child[0] == "not":
                raise VersionSyntaxError(f"double negation in {expression!r}")
            node: tuple[Any, ...] = ("not", child)
        elif token == "open":
            match("(")
            node = parse_operand("any", with_list=True)
            match(")")
        elif token == "set":
            node = ("set", parse_basic_set(next_basic_set(), relaxed=relaxed))
        else:
            raise VersionSyntaxError(f"unexpected symbol in {expression!r}")
        if with_list and next_token() in ("and", "or"):
            return parse_list(node, kind)
        return node

    def parse_list(head: tuple[Any, ...], kind: str) -> tuple[Any, ...]:
        if kind == "any":
            token = next_token()
            if token not in ("and", "or"):
                raise VersionSyntaxError(f"unexpected list operator in {expression!r}")
            return parse_list(head, token)
        if position < length and expression[position] == ("|" if kind == "and" else "&"):
            raise VersionSyntaxError(f"cannot mix '&' and '|' in {expression!r}; use parentheses")
        match("&" if kind == "and" else "|")
        return (kind, head, parse_operand(kind, with_list=True))

    tree = parse_operand("any", with_list=True)
    if next_token() != "end":
        raise VersionSyntaxError(f"trailing input in {expression!r}")
    return tree


def restriction_admits(version: Semver, restriction: tuple[str, Semver]) -> bool:
    operator, bound = restriction
    at_least = semver_precedes(bound, version) or semver_equivalent(bound, version)
    if operator == ">=":
        return at_least
    if operator == "<=":
        return semver_precedes(version, bound) or semver_equivalent(version, bound)
    if operator == "=":
        return semver_equivalent(version, bound)
    if operator == "/=":
        return not semver_equivalent(version, bound)
    if operator == "^":
        return at_least and bound.major == version.major
    #  '~' stays within the minor series, unlike most tilde implementations.
    return at_least and bound.major == version.major and bound.minor == version.minor


def version_set_admits(tree: tuple[Any, ...], version: Semver) -> bool:
    kind = tree[0]
    if kind == "set":
        return all(restriction_admits(version, restriction) for restriction in tree[1])
    if kind == "and":
        return version_set_admits(tree[1], version) and version_set_admits(tree[2], version)
    if kind == "or":
        return version_set_admits(tree[1], version) or version_set_admits(tree[2], version)
    return not version_set_admits(tree[1], version)


def requirement_admits(requirement: str, version: str) -> bool:
    """Whether VERSION satisfies REQUIREMENT, by Alire's version set rules.

    Raises VersionSyntaxError rather than guessing: a requirement the generator
    cannot read is a requirement it cannot report on.
    """
    return version_set_admits(
        parse_version_set(requirement), parse_semver(version, relaxed=True)
    )


def select_release(releases: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer the newest published release, falling back to the newest dev."""
    return next(
        (
            release
            for release in releases
            if not release.get("external")
            and not is_development_version(release["version"])
        ),
        releases[0],
    )


def release_for(package: dict[str, Any], version: str) -> dict[str, Any]:
    return next(release for release in package["versions"] if release["version"] == version)


def segment(value: str) -> str:
    return quote(value, safe="")


def path_segment(value: str) -> str:
    """Return the decoded filesystem name corresponding to one URL segment."""
    if not value or value in (".", "..") or "/" in value or "\\" in value:
        raise ValueError(f"unsafe path segment: {value!r}")
    return value


class SourceDocument(NamedTuple):
    """Markdown loaded from the immutable source tree of an indexed release."""

    markdown: str
    path: str
    repository_url: str
    commit: str


class ReleaseDocuments(NamedTuple):
    readme: SourceDocument | None
    changelog: SourceDocument | None
    changelog_remainder: SourceDocument | None


EMPTY_RELEASE_DOCUMENTS = ReleaseDocuments(None, None, None)


def source_repository_url(url: str) -> str:
    return url.removeprefix("git+")


def source_browser_url(document: SourceDocument) -> str | None:
    """Return a stable browser URL for a document when its host is GitHub."""
    repository = source_repository_url(document.repository_url).removesuffix(".git")
    parsed = urlsplit(repository)
    if parsed.netloc.lower() != "github.com":
        return None
    path = quote(document.path, safe="/")
    return f"{repository}/blob/{quote(document.commit, safe='')}/{path}"


def rewrite_source_url(value: str, document: SourceDocument, *, image: bool) -> str:
    """Resolve a Markdown URL against the pinned document in its source tree."""
    parsed_value = urlsplit(value)
    if parsed_value.scheme or parsed_value.netloc or value.startswith("#"):
        return value

    repository = source_repository_url(document.repository_url).removesuffix(".git")
    parsed_repository = urlsplit(repository)
    if parsed_repository.netloc.lower() != "github.com":
        return value

    document_parent = PurePosixPath(document.path).parent
    relative_path = parsed_value.path.lstrip("/")
    base = PurePosixPath() if parsed_value.path.startswith("/") else document_parent
    parts: list[str] = []
    for part in (base / relative_path).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    path = "/".join(quote(part, safe="") for part in parts)
    if image:
        owner_repository = parsed_repository.path.strip("/")
        rewritten = (
            f"https://raw.githubusercontent.com/{owner_repository}/"
            f"{quote(document.commit, safe='')}/{path}"
        )
    else:
        rewritten = (
            f"{repository}/blob/{quote(document.commit, safe='')}/{path}"
        )
    return urlunsplit((*urlsplit(rewritten)[:3], parsed_value.query, parsed_value.fragment))


def render_source_markdown(
    document: SourceDocument, *, anchor_prefix: str = ""
) -> str:
    """Render CommonMark safely, resolving relative links to the pinned tree."""
    try:
        from markdown_it import MarkdownIt
    except ImportError as error:
        raise RuntimeError(
            "markdown-it-py is required to render source documentation; "
            "install requirements-site.txt"
        ) from error

    renderer = MarkdownIt("commonmark", {"html": True}).enable(
        ["strikethrough", "table"]
    )
    renderer.renderer.rules["html_block"] = lambda *_args: ""
    renderer.renderer.rules["html_inline"] = lambda *_args: ""
    tokens = renderer.parse(document.markdown)
    heading_levels = [
        int(token.tag[1:])
        for token in tokens
        if token.type == "heading_open" and token.tag.startswith("h")
    ]
    heading_offset = 3 - min(heading_levels) if heading_levels else 0
    heading_slugs: dict[str, int] = defaultdict(int)
    for index, token in enumerate(tokens):
        if token.type in ("heading_open", "heading_close"):
            token.tag = f"h{min(6, int(token.tag[1:]) + heading_offset)}"
        if token.type == "heading_open" and index + 1 < len(tokens):
            inline = tokens[index + 1]
            visible_text = "".join(
                child.content
                for child in inline.children or []
                if child.type in ("text", "code_inline", "image")
            )
            base_slug = re.sub(r"[^\w\s-]", "", visible_text.lower()).strip()
            base_slug = re.sub(r"\s+", "-", base_slug) or "section"
            occurrence = heading_slugs[base_slug]
            heading_slugs[base_slug] += 1
            slug = base_slug if occurrence == 0 else f"{base_slug}-{occurrence}"
            if anchor_prefix:
                slug = f"{anchor_prefix}-{slug}"
            token.attrSet("id", slug)
        if token.type != "inline":
            continue
        for child in token.children or []:
            if child.type == "link_open":
                href = child.attrGet("href")
                if href is not None:
                    if anchor_prefix and href.startswith("#"):
                        href = f"#{anchor_prefix}-{href[1:]}"
                    child.attrSet(
                        "href", rewrite_source_url(href, document, image=False)
                    )
            elif child.type == "image":
                source = child.attrGet("src")
                if source is not None:
                    child.attrSet(
                        "src", rewrite_source_url(source, document, image=True)
                    )
    return renderer.renderer.render(tokens, renderer.options, {})


CHANGELOG_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def markdown_reference_definitions(markdown: str) -> str:
    """Return every CommonMark link definition without rendering it visibly."""
    try:
        from markdown_it import MarkdownIt
    except ImportError as error:
        raise RuntimeError(
            "markdown-it-py is required to read changelog links; "
            "install requirements-site.txt"
        ) from error

    environment: dict[str, Any] = {}
    MarkdownIt("commonmark").parse(markdown, environment)
    lines = markdown.splitlines()
    definitions = []
    seen: set[tuple[int, int]] = set()
    for reference in environment.get("references", {}).values():
        start, end = reference["map"]
        location = start, end
        if location in seen:
            continue
        seen.add(location)
        definitions.append("\n".join(lines[start:end]))
    return "\n".join(definitions)


def changelog_version_bounds(
    markdown: str, version: str
) -> tuple[int, int, int] | None:
    """Return heading start, body start, and section end for exactly VERSION."""
    version_pattern = re.compile(
        rf"^\s*\[?(?:(?:version|release)\s+)?v?{re.escape(version)}"
        rf"(?![0-9A-Za-z.+-])\]?(?:\([^)]*\))?(?:\s*(?:[-:/(]|$))",
        re.IGNORECASE,
    )
    headings = list(CHANGELOG_HEADING.finditer(markdown))
    for index, heading in enumerate(headings):
        if not version_pattern.search(heading.group(2)):
            continue
        level = len(heading.group(1))
        end = len(markdown)
        for following in headings[index + 1 :]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        definitions = markdown_reference_definitions(markdown)
        if definitions:
            definition_start = markdown.find(definitions)
            if heading.end() < definition_start < end:
                end = definition_start
        return heading.start(), heading.end(), end
    return None


def extract_changelog_version(markdown: str, version: str) -> str | None:
    """Extract one exact version while retaining global reference links."""
    bounds = changelog_version_bounds(markdown, version)
    if bounds is not None:
        _heading_start, body_start, end = bounds
        body = markdown[body_start:end].strip()
        if not body:
            return None
        definitions = markdown_reference_definitions(markdown)
        return f"{body}\n\n{definitions}" if definitions else body
    return None


def changelog_after_version(markdown: str, version: str) -> str | None:
    """Return only changelog entries following exactly VERSION's section."""
    bounds = changelog_version_bounds(markdown, version)
    if bounds is None:
        return None
    _heading_start, _body_start, end = bounds
    after = markdown[end:].lstrip()
    definitions = markdown_reference_definitions(markdown)
    visible = after.replace(definitions, "").strip() if definitions else after.strip()
    return after if visible else None


class SourceDocumentLoader:
    """Read release documentation from cached bare clones at pinned commits."""

    def __init__(self, cache: Path):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.repositories: dict[str, Path] = {}
        self.documents: dict[tuple[str, str, str, str], ReleaseDocuments] = {}
        self.commits: dict[str, list[str]] = defaultdict(list)

    def git(self, repository: Path | None, *args: str) -> str:
        command = ["git"]
        if repository is not None:
            command.extend(["-C", str(repository)])
        command.extend(args)
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "git failed"
            raise ValueError(f"source documentation: {detail}")
        return result.stdout

    def repository(self, url: str, commit: str) -> Path:
        clone_url = source_repository_url(url)
        repository = self.repositories.get(clone_url)
        if repository is None:
            digest = hashlib.sha256(clone_url.encode()).hexdigest()[:20]
            repository = self.cache / f"repository-{digest}.git"
            if not repository.exists():
                self.git(
                    None,
                    "clone",
                    "--bare",
                    "--filter=blob:none",
                    "--quiet",
                    clone_url,
                    str(repository),
                )
            self.repositories[clone_url] = repository
        exists = subprocess.run(
            ["git", "-C", str(repository), "cat-file", "-e", f"{commit}^{{commit}}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if exists.returncode:
            self.git(repository, "fetch", "--quiet", "origin", commit)
        return repository

    def tree_entries(self, repository: Path, commit: str, directory: str) -> list[str]:
        tree = f"{commit}:{directory}" if directory else commit
        return self.git(repository, "ls-tree", "--name-only", tree).splitlines()

    def find_document(
        self,
        repository: Path,
        repository_url: str,
        commit: str,
        subdir: str,
        filename: str,
    ) -> SourceDocument | None:
        directory = PurePosixPath(subdir) if subdir else PurePosixPath()
        if directory.is_absolute() or ".." in directory.parts:
            raise ValueError(f"unsafe source documentation subdir: {subdir!r}")
        while True:
            directory_text = "" if str(directory) == "." else directory.as_posix()
            entries = self.tree_entries(repository, commit, directory_text)
            match = next(
                (entry for entry in entries if entry.casefold() == filename.casefold()),
                None,
            )
            if match is not None:
                path = f"{directory_text}/{match}" if directory_text else match
                content = self.git(repository, "show", f"{commit}:{path}")
                return SourceDocument(content, path, repository_url, commit)
            if not directory_text:
                return None
            directory = directory.parent

    def load(self, manifest: dict[str, Any]) -> ReleaseDocuments:
        origin = manifest.get("origin")
        if not isinstance(origin, dict):
            return EMPTY_RELEASE_DOCUMENTS
        repository_url = origin.get("url")
        commit = origin.get("commit")
        subdir = origin.get("subdir", "")
        if not isinstance(repository_url, str) or not isinstance(commit, str):
            return EMPTY_RELEASE_DOCUMENTS
        if not isinstance(subdir, str):
            raise ValueError("source documentation origin subdir must be a string")
        if commit not in self.commits[repository_url]:
            self.commits[repository_url].append(commit)
        key = repository_url, commit, subdir, str(manifest["version"])
        if key in self.documents:
            return self.documents[key]
        repository = self.repository(repository_url, commit)
        readme = self.find_document(
            repository, repository_url, commit, subdir, "README.md"
        )
        changelog = self.find_document(
            repository, repository_url, commit, subdir, "CHANGELOG.md"
        )
        changelog_remainder = None
        if changelog is not None:
            full_changelog = changelog
            extracted = extract_changelog_version(
                full_changelog.markdown, manifest["version"]
            )
            remainder = changelog_after_version(
                full_changelog.markdown, manifest["version"]
            )
            changelog = (
                full_changelog._replace(markdown=extracted) if extracted else None
            )
            changelog_remainder = (
                full_changelog._replace(markdown=remainder) if remainder else None
            )
        documents = ReleaseDocuments(readme, changelog, changelog_remainder)
        self.documents[key] = documents
        return documents

    def fallback_changelog(
        self, manifest: dict[str, Any]
    ) -> tuple[SourceDocument, SourceDocument | None] | None:
        """Find VERSION in the same changelog path at another indexed commit."""
        origin = manifest.get("origin")
        if not isinstance(origin, dict):
            return None
        repository_url = origin.get("url")
        own_commit = origin.get("commit")
        subdir = origin.get("subdir", "")
        version = manifest.get("version")
        if not all(
            isinstance(value, str)
            for value in (repository_url, own_commit, subdir, version)
        ):
            return None
        for commit in self.commits.get(repository_url, []):
            if commit == own_commit:
                continue
            repository = self.repository(repository_url, commit)
            if subdir:
                exists = subprocess.run(
                    ["git", "-C", str(repository), "cat-file", "-e", f"{commit}:{subdir}"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if exists.returncode:
                    continue
            full_changelog = self.find_document(
                repository, repository_url, commit, subdir, "CHANGELOG.md"
            )
            if full_changelog is None:
                continue
            extracted = extract_changelog_version(full_changelog.markdown, version)
            if extracted is None:
                continue
            remainder = changelog_after_version(full_changelog.markdown, version)
            excerpt_document = full_changelog._replace(markdown=extracted)
            remainder_document = (
                full_changelog._replace(markdown=remainder) if remainder else None
            )
            return excerpt_document, remainder_document
        return None


def provided_identities(
    name: str, version: str, manifest: dict[str, Any]
) -> list[tuple[str, str]]:
    """Crate identities a release satisfies: its own plus every `provides` alias."""
    identities = {name.lower(): version}
    provides = manifest.get("provides") or []
    for entry in [provides] if isinstance(provides, str) else provides:
        alias, separator, provided = str(entry).partition("=")
        identities.setdefault(alias.strip().lower(), provided.strip() if separator else version)
    return list(identities.items())


def dependency_declarations(
    manifest: dict[str, Any],
) -> list[tuple[str, str, str | None]]:
    """Return every dependency and the conditional branch that declares it."""
    declarations: list[tuple[str, str, str | None]] = []

    def visit(mapping: dict[str, Any], conditions: tuple[str, ...] = ()) -> None:
        for name, value in mapping.items():
            if name.lower().startswith("case(") and isinstance(value, dict):
                for branch, branch_value in value.items():
                    if isinstance(branch_value, dict):
                        visit(
                            branch_value,
                            (*conditions, f"{name}={branch}"),
                        )
                continue
            if isinstance(value, str):
                declarations.append(
                    (name, value, " & ".join(conditions) or None)
                )

    for group in manifest.get("depends-on", []):
        if isinstance(group, dict):
            visit(group)
    return declarations


def dependency_index(packages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Map every required crate name to the indexed releases that require it."""
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for package in packages:
        for release in package["versions"]:
            for required, requirement, condition in dependency_declarations(
                release["manifest"]
            ):
                index[required.lower()].append(
                    {
                        "catalog": package.get("catalog", "flyology"),
                        "name": package["name"],
                        "version": release["version"],
                        "development": release["development"],
                        "selected": release["version"] == package["selected_version"],
                        "path": release["path"],
                        "requires": required.lower(),
                        "requirement": requirement,
                        "condition": condition,
                    }
                )
    return index


def attach_dependants(packages: list[dict[str, Any]]) -> None:
    """Record, on every release, the indexed releases that depend on it.

    A requirement is resolved against the version the release carries under the
    name being required, so a toolchain that declares `provides = ["gnat=16.2.0"]`
    is matched against 16.2.0 rather than against its own crate version.

    A version or requirement that cannot be parsed raises, naming the manifest
    at fault. Reporting a dependant relation wrongly is worse than not building
    the site, so an unreadable constraint stops generation instead of quietly
    weakening one row.
    """
    index = dependency_index(packages)
    for package in packages:
        for release in package["versions"]:
            found: dict[tuple[str, str, str, str, str | None], dict[str, Any]] = {}
            for required, provided_version in provided_identities(
                package["name"], release["version"], release["manifest"]
            ):
                records = index.get(required, [])
                if not records:
                    continue
                if release.get("external"):
                    for record in records:
                        if (
                            record["catalog"]
                            == package.get("catalog", "flyology")
                            and record["name"] == package["name"]
                        ):
                            continue
                        found.setdefault(
                            (
                                record["catalog"],
                                record["name"],
                                record["version"],
                                required,
                                record["condition"],
                            ),
                            {
                                **record,
                                "provided_version": None,
                                "qualifies": None,
                            },
                        )
                    continue
                try:
                    candidate = parse_semver(provided_version, relaxed=True)
                except VersionSyntaxError as error:
                    raise ValueError(
                        f"{release['path']} provides {required} {provided_version!r}, "
                        f"which is not a version Alire can parse: {error}"
                    ) from error
                for record in records:
                    if (
                        record["catalog"] == package.get("catalog", "flyology")
                        and record["name"] == package["name"]
                    ):
                        continue
                    try:
                        version_set = parse_version_set(record["requirement"])
                    except VersionSyntaxError as error:
                        raise ValueError(
                            f"{record['path']} requires {required} = "
                            f"{record['requirement']!r}, which is not a version set "
                            f"Alire can parse: {error}"
                        ) from error
                    found.setdefault(
                        (
                            record["catalog"],
                            record["name"],
                            record["version"],
                            required,
                            record["condition"],
                        ),
                        {
                            **record,
                            "provided_version": provided_version,
                            "qualifies": version_set_admits(version_set, candidate),
                        },
                    )
            dependants: list[dict[str, Any]] = []
            grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for record in found.values():
                grouped[(record["catalog"], record["name"])].append(record)
            for identity in sorted(grouped):
                dependants.extend(
                    sorted(
                        grouped[identity],
                        key=lambda record: version_key(record["version"]),
                        reverse=True,
                    )
                )
            release["dependants"] = dependants


def load_catalog(
    source: Path,
    *,
    catalog_name: str = "flyology",
    canonical_url: str = CANONICAL_URL,
    repository_url: str = REPOSITORY_URL,
    attach_relationships: bool = True,
) -> dict[str, Any]:
    index_path = source / "index.toml"
    with index_path.open("rb") as stream:
        index_metadata = json_value(tomllib.load(stream))

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for manifest_path in sorted(source.glob("*/*/*.toml")):
        with manifest_path.open("rb") as stream:
            manifest = json_value(tomllib.load(stream))
        try:
            name = manifest["name"]
        except KeyError as error:
            raise ValueError(f"{manifest_path} is missing 'name'") from error
        external = "version" not in manifest and bool(manifest.get("external"))
        if "version" not in manifest and not external:
            raise ValueError(f"{manifest_path} is missing 'version'")
        identity = name, manifest.get("version", "system")
        if identity in seen:
            raise ValueError(f"duplicate manifest for {identity[0]} {identity[1]}")
        seen.add(identity)
        grouped[identity[0]].append(
            {
                "version": identity[1],
                "development": is_development_version(identity[1]),
                "external": external,
                "path": manifest_path.relative_to(source.parent).as_posix(),
                "manifest": manifest,
            }
        )

    packages = []
    for name, releases in sorted(grouped.items()):
        releases.sort(
            key=lambda release: (
                not release.get("external", False),
                version_key(release["version"])
                if not release.get("external")
                else ((0,), False, ()),
            ),
            reverse=True,
        )
        selected = select_release(releases)
        development_only = all(release["development"] for release in releases)
        packages.append(
            {
                "catalog": catalog_name,
                "name": name,
                "description": selected["manifest"].get("description", "No description provided."),
                "latest_version": releases[0]["version"],
                "selected_version": selected["version"],
                "development_only": development_only,
                "versions": releases,
            }
        )
    if attach_relationships:
        attach_dependants(packages)
        attach_resolved_dependencies(packages)

    revision = git_output(source.parent, "rev-parse", "HEAD")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at(),
        "canonical_url": canonical_url,
        "catalog": {
            "name": catalog_name,
            "repository_url": repository_url,
            "revision": revision.strip() if revision else None,
        },
        "index": index_metadata,
        "packages": packages,
    }


def git_output(repository: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout


def manifest_at(repository: Path, revision: str, path: str) -> dict[str, Any] | None:
    raw = git_output(repository, "show", f"{revision}:{path}")
    if raw is None:
        return None
    try:
        return json_value(tomllib.loads(raw))
    except tomllib.TOMLDecodeError:
        return None


def dependency_map(manifest: dict[str, Any]) -> dict[str, str]:
    dependencies: dict[str, str] = {}
    for name, constraint, condition in dependency_declarations(manifest):
        key = f"{name} ({condition})" if condition else name
        dependencies[key] = constraint
    return dependencies


def dependency_changes(
    before: dict[str, Any] | None, after: dict[str, Any]
) -> list[dict[str, str]]:
    previous = dependency_map(before or {})
    current = dependency_map(after)
    changes = []
    for name in sorted(previous.keys() | current.keys()):
        if name not in previous:
            changes.append({"kind": "added", "name": name, "after": current[name]})
        elif name not in current:
            changes.append({"kind": "removed", "name": name, "before": previous[name]})
        elif previous[name] != current[name]:
            changes.append(
                {
                    "kind": "changed",
                    "name": name,
                    "before": previous[name],
                    "after": current[name],
                }
            )
    return changes


def source_revision(manifest: dict[str, Any] | None) -> tuple[str | None, str | None]:
    origin = (manifest or {}).get("origin")
    if not isinstance(origin, dict):
        return None, None
    url = origin.get("url")
    commit = origin.get("commit")
    return (str(url) if url else None, str(commit) if commit else None)


def source_web_url(url: str | None) -> str | None:
    if not url:
        return None
    web_url = url.removeprefix("git+")
    return web_url.removesuffix(".git")


def solution_summary(message: str, subject: str) -> str:
    for line in message.splitlines():
        if line.startswith("Solution:"):
            return line.removeprefix("Solution:").strip()
    return subject.removeprefix("Problem:").strip()


def change_entry(
    status: str,
    path: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    package_added: bool = False,
) -> dict[str, Any] | None:
    try:
        name = str(after["name"])
        version = str(after["version"])
    except KeyError:
        return None
    development = is_development_version(version)
    if status == "A" and package_added:
        kind = "package"
    elif status == "A":
        kind = "published"
    elif development:
        kind = "development"
    else:
        kind = "manifest"
    before_url, before_revision = source_revision(before)
    after_url, after_revision = source_revision(after)
    changed_fields = (
        []
        if status == "A"
        else sorted(
            key
            for key in (before or {}).keys() | after.keys()
            if (before or {}).get(key) != after.get(key)
        )
    )
    return {
        "kind": kind,
        "name": name,
        "version": version,
        "development": development,
        "description": after.get("description", "No description provided."),
        "path": path,
        "manifest": after,
        "changed_fields": changed_fields,
        "dependency_changes": dependency_changes(before, after),
        "before_source_url": before_url,
        "before_revision": before_revision,
        "source_url": after_url,
        "revision": after_revision,
    }


def load_change_history(
    catalog: dict[str, Any], repository: Path = ROOT
) -> list[dict[str, Any]]:
    manifest_paths = sorted(
        release["path"]
        for package in catalog["packages"]
        for release in package["versions"]
    )
    if not manifest_paths:
        return []
    log = git_output(
        repository,
        "log",
        f"--max-count={CHANGE_HISTORY_LIMIT}",
        "--format=%H%x09%cI%x09%s",
        "--",
        *manifest_paths,
    )
    if not log:
        return []
    history = []
    current_paths = set(manifest_paths)
    for line in log.splitlines():
        try:
            commit, timestamp, subject = line.split("\t", 2)
        except ValueError:
            continue
        parent = git_output(repository, "rev-parse", f"{commit}^")
        if not parent:
            continue
        parent = parent.strip()
        diff = git_output(
            repository,
            "diff",
            "--name-status",
            "--diff-filter=AM",
            parent,
            commit,
            "--",
            *manifest_paths,
        )
        if not diff:
            continue
        entries = []
        for changed in diff.splitlines():
            try:
                status, path = changed.split("\t", 1)
            except ValueError:
                continue
            if path not in current_paths:
                continue
            after = manifest_at(repository, commit, path)
            if after is None:
                continue
            before = manifest_at(repository, parent, path) if status == "M" else None
            package_tree = None
            if status == "A":
                package_tree = git_output(
                    repository,
                    "ls-tree",
                    "-r",
                    "--name-only",
                    parent,
                    "--",
                    str(PurePosixPath(path).parent),
                )
            entry = change_entry(
                status,
                path,
                before,
                after,
                package_added=status == "A" and not (package_tree or "").strip(),
            )
            if entry is not None:
                entries.append(entry)
        if not entries:
            continue
        message = git_output(repository, "show", "-s", "--format=%B", commit) or subject
        history.append(
            {
                "commit": commit,
                "timestamp": timestamp,
                "subject": subject,
                "summary": solution_summary(message, subject),
                "entries": sorted(entries, key=lambda entry: (entry["name"], version_key(entry["version"]))),
            }
        )
    return history


def text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(text(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def field(label: str, value: Any, *, link: bool = False) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    escaped = html.escape(text(value))
    href = None
    if link and isinstance(value, str):
        parsed = urlsplit(value)
        if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
            href = value
        elif not parsed.scheme and not parsed.netloc and re.fullmatch(
            r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s]*)?", value
        ):
            href = f"https://{value}"
    rendered = (
        f'<a href="{html.escape(href, quote=True)}">{escaped}</a>'
        if href is not None
        else escaped
    )
    return f"<div><dt>{html.escape(label)}</dt><dd>{rendered}</dd></div>"


def matching_dependency_release(
    packages: list[dict[str, Any]], required: str, requirement: str
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    """Return the highest matching release from the first qualifying index."""
    catalog_order = list(dict.fromkeys(package.get("catalog", "flyology") for package in packages))
    for catalog_name in catalog_order:
        best: tuple[dict[str, Any], dict[str, Any], str, Semver] | None = None
        external_target: tuple[dict[str, Any], dict[str, Any], str] | None = None
        for package in packages:
            if package.get("catalog", "flyology") != catalog_name:
                continue
            for release in package["versions"]:
                for identity, provided_version in provided_identities(
                    package["name"], release["version"], release["manifest"]
                ):
                    if identity != required.lower():
                        continue
                    if release.get("external"):
                        external_target = package, release, "system"
                        continue
                    candidate = parse_semver(provided_version, relaxed=True)
                    if not requirement_admits(requirement, provided_version):
                        continue
                    if best is None or semver_precedes(best[3], candidate):
                        best = package, release, provided_version, candidate
                        continue
                    if semver_equivalent(best[3], candidate) and version_key(
                        best[1]["version"]
                    ) < version_key(release["version"]):
                        best = package, release, provided_version, candidate
        if best is not None:
            return best[0], best[1], best[2]
        if external_target is not None:
            return external_target
    return None


def attach_resolved_dependencies(packages: list[dict[str, Any]]) -> None:
    """Attach one ordered-index resolution record per declared dependency."""
    for package in packages:
        for release in package["versions"]:
            resolved = []
            for name, requirement, condition in dependency_declarations(
                release["manifest"]
            ):
                target = matching_dependency_release(packages, name, requirement)
                record: dict[str, Any] = {
                    "name": name,
                    "requirement": requirement,
                    "condition": condition,
                    "resolved": target is not None,
                    "catalog": None,
                    "package": None,
                    "version": None,
                    "provided_version": None,
                }
                if target is not None:
                    target_package, target_release, provided_version = target
                    record.update(
                        {
                            "catalog": target_package.get("catalog", "flyology"),
                            "package": target_package["name"],
                            "version": target_release["version"],
                            "provided_version": provided_version,
                        }
                    )
                resolved.append(record)
            release["dependencies"] = resolved


def relationship_href(
    root_prefix: str,
    current_catalog: str,
    target_catalog: str,
    package_name: str,
    version: str,
) -> str:
    if current_catalog == target_catalog:
        catalog_prefix = ""
    elif current_catalog == "flyology" and target_catalog == "community":
        catalog_prefix = "community/"
    elif current_catalog == "community" and target_catalog == "flyology":
        catalog_prefix = "../"
    else:
        raise ValueError(
            f"cannot link from catalog {current_catalog!r} to {target_catalog!r}"
        )
    return (
        f"{root_prefix}{catalog_prefix}crates/{segment(package_name)}/"
        f"{segment(version)}/"
    )


def dependency_rows(
    release: dict[str, Any],
    *,
    root_prefix: str = "",
    current_catalog: str = "flyology",
) -> str:
    dependencies = release.get("dependencies", [])
    if not dependencies:
        return '<p class="quiet">No package dependencies declared.</p>'
    rows = []
    for dependency in dependencies:
        catalog_name = dependency["catalog"]
        resolved_label = (
            f'{"Community" if catalog_name == "community" else "Flyology"} · '
            f'{dependency["version"]}'
            if dependency["resolved"]
            else "Unresolved"
        )
        condition = (
            f'<small class="dependency-condition">{html.escape(dependency["condition"])}</small>'
            if dependency["condition"]
            else ""
        )
        content = (
            '<span class="dependency-identity">'
            f'<code>{html.escape(dependency["name"])}</code>'
            f'<small>{html.escape(resolved_label)}</small></span>'
            '<span class="dependency-requirement">'
            f'{html.escape(dependency["requirement"])}{condition}</span>'
        )
        if not dependency["resolved"]:
            row = f'<span class="dependency-row">{content}</span>'
        else:
            href = relationship_href(
                root_prefix,
                current_catalog,
                catalog_name,
                dependency["package"],
                dependency["version"],
            )
            row = (
                f'<a class="dependency-row dependency-link" '
                f'href="{html.escape(href, quote=True)}">{content}</a>'
            )
        rows.append(f"<li>{row}</li>")
    return '<ul class="dependency-list">' + "".join(rows) + "</ul>"


def counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def dependant_verdict(qualifies: bool | None) -> tuple[str, str]:
    """The CSS state and the label describing one dependant's requirement."""
    if qualifies is None:
        return "system", "System"
    return ("qualifies", "Qualifies") if qualifies else ("excluded", "Excluded")


def dependant_rows(
    package_name: str,
    release: dict[str, Any],
    root_prefix: str,
    *,
    current_catalog: str = "flyology",
) -> str:
    dependants = release["dependants"]
    if not dependants:
        return '<p class="quiet">No indexed release depends on this version.</p>'
    identities: list[str] = []
    for record in dependants:
        provided = record["provided_version"] or "system-detected"
        identity = f'{record["requires"]} {provided}'
        if identity not in identities:
            identities.append(identity)
    qualifying = sum(record["qualifies"] is True for record in dependants)
    tested = ", ".join(f"<code>{html.escape(identity)}</code>" for identity in identities)
    summary = (
        f"Resolved against {tested} — {qualifying} of "
        f'{counted(len(dependants), "dependant release")} '
        f'{"qualifies" if qualifying == 1 else "qualify"}.'
    )
    groups = []
    for (catalog_name, name), record_group in groupby(
        dependants, key=lambda record: (record["catalog"], record["name"])
    ):
        records = list(record_group)
        rows = []
        for record in records:
            state, label = dependant_verdict(record["qualifies"])
            version = html.escape(record["version"])
            if record["selected"]:
                version = (
                    f"<strong>{version}</strong>"
                    '<span class="visually-hidden"> (selected version)</span>'
                )
            requirement = record["requirement"]
            if record["requires"] != package_name.lower():
                requirement = f'{record["requires"]} {requirement}'
            if record["condition"]:
                requirement = f'{requirement} · {record["condition"]}'
            href = relationship_href(
                root_prefix,
                current_catalog,
                record["catalog"],
                record["name"],
                record["version"],
            )
            rows.append(
                f'<li class="dependant-release dependant-release-{state}">'
                f'<a href="{html.escape(href, quote=True)}">{version}</a>'
                f"<code>{html.escape(requirement)}</code>"
                f'<span class="dependant-verdict">{label}</span>'
                "</li>"
            )
        top_match = next(
            (record for record in records if record["qualifies"]), records[0]
        )
        top_release_href = relationship_href(
            root_prefix,
            current_catalog,
            top_match["catalog"],
            name,
            top_match["version"],
        )
        catalog_label = "Community" if catalog_name == "community" else "Flyology"
        groups.append(
            '<li class="dependant-group">'
            f'<a class="dependant-name" href="{html.escape(top_release_href, quote=True)}">{html.escape(name)}</a>'
            f'<span class="dependant-catalog">{catalog_label}</span>'
            f'<ul class="dependant-releases">{"".join(rows)}</ul>'
            "</li>"
        )
    return (
        f'<p class="quiet dependant-summary">{summary}</p>'
        f'<ul class="dependant-list">{"".join(groups)}</ul>'
    )


def origin_summary(manifest: dict[str, Any]) -> str:
    origin = manifest.get("origin")
    if not origin:
        return "Not declared"
    if isinstance(origin, dict) and "url" in origin:
        commit = f" @ {origin['commit'][:12]}" if origin.get("commit") else ""
        return f"{origin['url']}{commit}"
    return "Platform-specific binary archives"


def package_kind(manifest: dict[str, Any]) -> str:
    if manifest.get("external"):
        return "external"
    return "toolchain" if manifest.get("provides") or manifest.get("auto-gpr-with") is False else "source"


def status_badge(release: dict[str, Any], *, development_only: bool = False) -> str:
    if not release["development"]:
        return ""
    label = "Development only" if development_only else "Development"
    return f'<span class="release-status release-status-development">{label}</span>'


def release_metadata(release: dict[str, Any]) -> str:
    manifest = release["manifest"]
    return "".join(
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


def render_release_detail(
    package_name: str,
    release: dict[str, Any],
    *,
    heading_level: int,
    raw_expanded: bool,
    root_prefix: str,
    current_catalog: str = "flyology",
    include_relationships: bool = True,
    include_raw_manifest: bool = True,
) -> str:
    manifest = release["manifest"]
    heading = f"h{heading_level}"
    child_heading = f"h{min(heading_level + 1, 6)}"
    relationships = ""
    if include_relationships:
        relationships = f"""
        <section class="detail-section" aria-labelledby="deps-{html.escape(package_name)}-{html.escape(release['version'])}">
          <{child_heading} id="deps-{html.escape(package_name)}-{html.escape(release['version'])}">Dependencies</{child_heading}>
          {dependency_rows(release, root_prefix=root_prefix, current_catalog=current_catalog)}
        </section>
        <section class="detail-section" aria-labelledby="dependants-{html.escape(package_name)}-{html.escape(release['version'])}">
          <{child_heading} id="dependants-{html.escape(package_name)}-{html.escape(release['version'])}">Dependants</{child_heading}>
          {dependant_rows(package_name, release, root_prefix, current_catalog=current_catalog)}
        </section>"""
    raw_manifest = ""
    if include_raw_manifest:
        raw = html.escape(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True)
        )
        raw_manifest = f"""
        <details class="raw-manifest"{' open' if raw_expanded else ''}>
          <summary>Complete manifest as JSON</summary>
          <pre><code>{raw}</code></pre>
        </details>"""
    return f"""
      <section class="release-detail" aria-labelledby="release-{html.escape(package_name)}-{html.escape(release['version'])}">
        <div class="release-heading">
          <{heading} id="release-{html.escape(package_name)}-{html.escape(release['version'])}">{html.escape(release['version'])}</{heading}>
          {status_badge(release)}
        </div>
        <dl class="metadata">{release_metadata(release)}</dl>
        {relationships}
        {raw_manifest}
      </section>"""


def version_href(package: dict[str, Any], release: dict[str, Any], context: str) -> str:
    name = segment(package["name"])
    version = segment(release["version"])
    if context == "home":
        return f"crates/{name}/{version}/"
    if context == "package":
        return f"{version}/"
    if context == "version":
        return f"../{version}/"
    raise ValueError(f"unknown version-link context: {context}")


def render_version_links(
    package: dict[str, Any],
    *,
    context: str,
    current_version: str,
    exclude_current: bool,
    title: str,
) -> str:
    releases = [
        release
        for release in package["versions"]
        if not exclude_current or release["version"] != current_version
    ]
    if not releases:
        return f'<section class="version-index"><h3>{html.escape(title)}</h3><p class="quiet">No other indexed versions.</p></section>'
    items = []
    for release in releases:
        current = release["version"] == current_version
        current_text = "Current page" if context == "version" else "Selected"
        current_label = f'<span class="current-label">{current_text}</span>' if current else ""
        current_attribute = ' aria-current="page"' if current and context == "version" else ""
        items.append(
            f'<li><a href="{html.escape(version_href(package, release, context), quote=True)}"'
            f'{current_attribute}>'
            f'<span>{html.escape(release["version"])}</span>'
            f'<span class="version-link-status">{status_badge(release)}{current_label}</span>'
            "</a></li>"
        )
    return f'<section class="version-index"><h3>{html.escape(title)}</h3><ul class="version-links">{"".join(items)}</ul></section>'


def render_detail_rail(
    package: dict[str, Any],
    release: dict[str, Any],
    *,
    context: str,
    root_prefix: str,
    manifest_href: str,
    current_catalog: str = "flyology",
) -> str:
    name = package["name"]
    version = release["version"]
    identity = f"{html.escape(name)}-{html.escape(version)}"
    return f"""
      <aside class="detail-page-rail" aria-label="Version relationships and downloads">
        {render_version_links(package, context=context, current_version=version, exclude_current=False, title='Indexed versions')}
        <section class="detail-rail-section" aria-labelledby="rail-deps-{identity}">
          <h3 id="rail-deps-{identity}">Dependencies</h3>
          {dependency_rows(release, root_prefix=root_prefix, current_catalog=current_catalog)}
        </section>
        <section class="detail-rail-section" aria-labelledby="rail-dependants-{identity}">
          <h3 id="rail-dependants-{identity}">Dependants</h3>
          {dependant_rows(name, release, root_prefix, current_catalog=current_catalog)}
        </section>
        <a class="manifest-json-link" href="{html.escape(manifest_href, quote=True)}" download>Complete manifest as JSON</a>
      </aside>"""


def render_package(
    package: dict[str, Any], *, current_catalog: str = "flyology"
) -> str:
    selected = release_for(package, package["selected_version"])
    manifest = selected["manifest"]
    tags = manifest.get("tags", [])
    kind = package_kind(manifest)
    search = " ".join(
        [package["name"], package["description"], package["selected_version"], *tags]
    ).lower()
    tag_html = "".join(f"<li>{html.escape(tag)}</li>" for tag in tags)
    name = segment(package["name"])
    version = segment(selected["version"])
    return f"""
    <details class="package" data-package data-kind="{kind}" data-search="{html.escape(search, quote=True)}">
      <summary class="package-summary">
        <span class="package-identity">
          <span class="package-name">{html.escape(package['name'])}</span>
          <span class="version-badge">{html.escape(package['selected_version'])}</span>
          {status_badge(selected, development_only=package['development_only'])}
        </span>
        <span class="package-description">{html.escape(package['description'])}</span>
        <span class="summary-action" aria-hidden="true">Details <span>+</span></span>
      </summary>
      <div class="package-body">
        <div class="package-facts">
          <span class="kind-label">{kind.capitalize()}</span>
          <ul class="tag-list" aria-label="Tags">{tag_html}</ul>
          <span class="package-links">
            <a href="crates/{name}/">Crate page</a>
            <a href="crates/{name}/{version}/">Version page</a>
            <a href="crates/{name}.json" download>JSON</a>
          </span>
        </div>
        <div class="package-release-layout">
          {render_release_detail(package['name'], selected, heading_level=3, raw_expanded=False, root_prefix='', current_catalog=current_catalog, include_relationships=False, include_raw_manifest=False)}
          {render_detail_rail(
              package,
              selected,
              context='home',
              root_prefix='',
              manifest_href=f'crates/{name}/{version}/manifest.json',
              current_catalog=current_catalog,
          )}
        </div>
      </div>
    </details>"""


def change_kind_label(kind: str) -> str:
    return {
        "package": "New crate",
        "published": "New version",
        "development": "Development update",
        "manifest": "Manifest update",
    }[kind]


def change_date_label(timestamp: str) -> str:
    try:
        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp[:10]
    return f"{instant.strftime('%b')} {instant.day}, {instant.year}"


def source_link(url: str | None, revision: str | None, label: str) -> str:
    web_url = source_web_url(url)
    if web_url and revision:
        return f'<a href="{html.escape(f"{web_url}/commit/{revision}", quote=True)}"><code>{html.escape(label)}</code></a>'
    if revision:
        return f"<code>{html.escape(label)}</code>"
    return '<span class="quiet">Not applicable</span>'


def render_dependency_changes(changes: list[dict[str, str]]) -> str:
    if not changes:
        return ""
    items = []
    for change in changes:
        name = f'<code>{html.escape(change["name"])}</code>'
        if change["kind"] == "added":
            detail = f'Added {name} <code>{html.escape(change["after"])}</code>'
        elif change["kind"] == "removed":
            detail = f'Removed {name} <code>{html.escape(change["before"])}</code>'
        else:
            detail = (
                f'{name} <code>{html.escape(change["before"])}</code>'
                f'<span aria-hidden="true">→</span><span class="visually-hidden"> changed to </span>'
                f'<code>{html.escape(change["after"])}</code>'
            )
        items.append(f'<li class="dependency-change-{change["kind"]}">{detail}</li>')
    return f'<div class="change-dependencies"><h4>Dependency changes</h4><ul>{"".join(items)}</ul></div>'


def render_change_entry(
    entry: dict[str, Any],
    *,
    root_prefix: str,
    detailed: bool,
    repository_url: str = REPOSITORY_URL,
    repository_revision: str = "main",
) -> str:
    name = segment(entry["name"])
    version = segment(entry["version"])
    label = change_kind_label(entry["kind"])
    crate_href = f'{root_prefix}crates/{name}/{version}/'
    fields = ", ".join(entry["changed_fields"])
    before_revision = entry["before_revision"]
    revision = entry["revision"]
    if entry["kind"] == "development" and before_revision and revision:
        concise = (
            f'{source_link(entry["before_source_url"], before_revision, before_revision[:8])}'
            f'<span aria-hidden="true">→</span><span class="visually-hidden"> updated to </span>'
            f'{source_link(entry["source_url"], revision, revision[:8])}'
        )
    elif entry["kind"] == "package":
        concise = "Crate added to the index"
    elif entry["kind"] == "published":
        concise = "Version added to the index"
    else:
        concise = f"Changed {html.escape(fields)}" if fields else "Manifest metadata changed"
    details = ""
    if detailed:
        facts = []
        if entry["kind"] in ("package", "published"):
            facts.append(f'<div><dt>Origin</dt><dd>{html.escape(origin_summary(entry["manifest"]))}</dd></div>')
        elif before_revision or revision:
            facts.append(f'<div><dt>Source revision</dt><dd class="change-revisions">{concise}</dd></div>')
        if fields:
            facts.append(f'<div><dt>Manifest fields</dt><dd>{html.escape(fields)}</dd></div>')
        facts.append(
            f'<div><dt>Manifest</dt><dd><a href="{repository_url}/blob/{html.escape(repository_revision, quote=True)}/{html.escape(entry["path"], quote=True)}"><code>{html.escape(entry["path"])}</code></a></dd></div>'
        )
        comparison = ""
        before_web = source_web_url(entry["before_source_url"])
        after_web = source_web_url(entry["source_url"])
        if before_web and before_web == after_web and before_revision and revision:
            compare_href = f"{after_web}/compare/{before_revision}...{revision}"
            comparison = f'<a class="change-compare" href="{html.escape(compare_href, quote=True)}">Compare source revisions</a>'
        details = f"""
          <p class="change-description">{html.escape(entry['description'])}</p>
          <dl class="change-facts">{"".join(facts)}</dl>
          {render_dependency_changes(entry['dependency_changes'])}
          {comparison}"""
    return f"""
      <article class="change-entry change-entry-{html.escape(entry['kind'])}">
        <div class="change-entry-heading">
          <span class="change-kind">{html.escape(label)}</span>
          <h3><a href="{html.escape(crate_href, quote=True)}">{html.escape(entry['name'])} <code>{html.escape(entry['version'])}</code></a></h3>
        </div>
        <div class="change-entry-summary">{concise}</div>
        {details}
      </article>"""


def render_change_preview(
    catalog: dict[str, Any], history: list[dict[str, Any]]
) -> str:
    all_entries = []
    for group in history:
        for entry in group["entries"]:
            all_entries.append((group, entry))
    recent = all_entries[:HOME_CHANGE_LIMIT]
    for required_kinds in (("package", "published"), ("development", "manifest")):
        if any(entry["kind"] in required_kinds for _group, entry in recent):
            continue
        replacement = next(
            (
                item
                for item in all_entries[HOME_CHANGE_LIMIT:]
                if item[1]["kind"] in required_kinds
            ),
            None,
        )
        if replacement and recent:
            recent[-1] = replacement
    if not recent:
        return ""
    rows = []
    repository_url = catalog["catalog"]["repository_url"]
    repository_revision = catalog["catalog"].get("revision") or "main"
    for group, entry in recent:
        rows.append(
            f'<li><time datetime="{html.escape(group["timestamp"], quote=True)}">{html.escape(change_date_label(group["timestamp"]))}</time>'
            f'{render_change_entry(entry, root_prefix="", detailed=False, repository_url=repository_url, repository_revision=repository_revision)}</li>'
        )
    return f"""
      <section class="changes-preview page-shell" aria-labelledby="changes-preview-title">
        <div class="changes-heading">
          <div><p class="eyebrow">Index activity</p><h2 id="changes-preview-title">Recent changes.</h2></div>
          <p>Crates and versions recently added or updated in this index.</p>
        </div>
        <ol class="change-preview-list">{"".join(rows)}</ol>
        <a class="text-link" href="changes/">View the detailed change history <span aria-hidden="true">→</span></a>
      </section>"""


def other_catalog_href(root_prefix: str, catalog_name: str) -> str:
    return (
        f"{root_prefix}community/"
        if catalog_name == "flyology"
        else f"{root_prefix}../"
    )


def render_site_header(
    root_prefix: str, current: str, catalog: dict[str, Any]
) -> str:
    catalog_name = catalog["catalog"]["name"]
    repository_url = catalog["catalog"]["repository_url"]
    brand_label = "Community Crates" if catalog_name == "community" else "Flyology Crates"
    other_label = "Flyology index" if catalog_name == "community" else "Community index"
    package_current = ' aria-current="page"' if current == "packages" else ""
    changes_current = ' aria-current="page"' if current == "changes" else ""
    return f"""
    <header class="site-header">
      <nav class="site-nav" aria-label="Primary navigation">
        <a class="brand" href="{root_prefix}" aria-label="{brand_label} home">
          <img class="brand-mark" src="{root_prefix}flyology-mark.svg" alt="">
          <span>{brand_label}</span>
        </a>
        <ul class="nav-links" data-nav-links>
          <li><a href="{root_prefix}#catalog"{package_current}>Packages</a></li>
          <li><a href="{root_prefix}changes/"{changes_current}>Changes</a></li>
          <li><a class="catalog-switch" href="{other_catalog_href(root_prefix, catalog_name)}"><span>{other_label}</span><span aria-hidden="true">↗</span></a></li>
          <li><a href="{root_prefix}crates.json" download>JSON</a></li>
          <li><a href="{repository_url}">GitHub</a></li>
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
    </header>"""


def render_html(catalog: dict[str, Any], history: list[dict[str, Any]]) -> str:
    packages = catalog["packages"]
    catalog_name = catalog["catalog"]["name"]
    is_community = catalog_name == "community"
    repository_url = catalog["catalog"]["repository_url"]
    manifest_count = sum(len(package["versions"]) for package in packages)
    source_count = sum(package_kind(package["versions"][0]["manifest"]) == "source" for package in packages)
    package_html = "".join(
        render_package(package, current_catalog=catalog_name) for package in packages
    )
    page_description = (
        "A daily generated shadow of packages in the Alire community index."
        if is_community
        else "Packages and compiler builds published in the Flyology Alire index."
    )
    page_title = "Alire Community Shadow" if is_community else "Flyology Crate Index"
    eyebrow = "Alire community mirror" if is_community else "Flyology packages"
    heading = "Alire community index." if is_community else "Flyology Alire index."
    lede = (
        "A daily shadow of the Alire community catalog, rendered with the same package, version, relationship, and JSON views as the Flyology index."
        if is_community
        else "Packages and compiler builds maintained by Flyology. This page and its JSON files are generated directly from the index manifests."
    )
    install_command = (
        "alr index --reset-community"
        if is_community
        else """alr index \\
<span class="install-option">  --add=</span>git+https://github.com/flyology-ada/alire-index.git \\
<span class="install-option">  --name=</span>flyology \\
<span class="install-option">  --before=</span>community"""
    )
    install_label = "Restore community index" if is_community else "Configure Alire"
    repository_label = "Alire community index" if is_community else "Flyology Alire index"
    other_label = "Flyology index" if is_community else "Community shadow"
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="{html.escape(page_description, quote=True)}">
    <meta name="theme-color" content="#17213d">
    <title>{page_title}</title>
    <link rel="canonical" href="{catalog['canonical_url']}">
    <link rel="icon" href="flyology-logo.svg" type="image/svg+xml">
    <link rel="stylesheet" href="assets/styles/site.css">
    <link rel="stylesheet" href="assets/styles/index.css?v={INDEX_CSS_VERSION}">
    <script src="assets/scripts/ada-highlight.js"></script>
    <script src="assets/scripts/site.js"></script>
    <script src="assets/scripts/index.js" defer></script>
  </head>
  <body>
    <a class="skip-link" href="#catalog">Skip to crate catalog</a>
    {render_site_header('./', 'packages', catalog)}
    <main>
      <section class="catalog-hero page-shell" aria-labelledby="page-title">
        <div>
          <p class="eyebrow">{eyebrow}</p>
          <h1 id="page-title">{heading}</h1>
          <p class="hero-lede">{lede}</p>
          <div class="actions">
            <a class="button button-primary" href="#catalog">View {len(packages)} packages</a>
            <a class="button button-secondary" href="crates.json" download>Download JSON</a>
          </div>
        </div>
        <div class="install-panel">
          <div class="install-heading"><span>{install_label}</span><span>shell</span></div>
          <pre><code id="install-command">{install_command}</code></pre>
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
      {render_change_preview(catalog, history)}
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
              <option value="external">System externals</option>
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
        <p>Generated from the <a href="{repository_url}">{repository_label}</a>.</p>
        <div class="footer-links"><a href="{other_catalog_href('./', catalog_name)}">{other_label}</a><a href="crates.json">Aggregate JSON</a><a href="README.txt">JSON schema notes</a></div>
      </div>
    </footer>
  </body>
</html>
"""


def render_detail_header(root_prefix: str, catalog: dict[str, Any]) -> str:
    return render_site_header(root_prefix, "packages", catalog)


def render_changes_page(
    catalog: dict[str, Any], history: list[dict[str, Any]]
) -> str:
    catalog_name = catalog["catalog"]["name"]
    repository_url = catalog["catalog"]["repository_url"]
    repository_revision = catalog["catalog"].get("revision") or "main"
    catalog_label = (
        "Alire community index" if catalog_name == "community" else "Flyology Alire index"
    )
    change_count = sum(len(group["entries"]) for group in history)
    published_count = sum(
        entry["kind"] in ("package", "published")
        for group in history
        for entry in group["entries"]
    )
    development_count = sum(
        entry["kind"] == "development" for group in history for entry in group["entries"]
    )
    groups = []
    for group in history:
        entries = "".join(
            render_change_entry(
                entry,
                root_prefix="../",
                detailed=True,
                repository_url=repository_url,
                repository_revision=repository_revision,
            )
            for entry in group["entries"]
        )
        groups.append(
            f"""
        <li class="change-group">
          <header class="change-group-heading">
            <time datetime="{html.escape(group['timestamp'], quote=True)}">{html.escape(change_date_label(group['timestamp']))}</time>
            <div>
              <h2>{html.escape(group['summary'])}</h2>
              <a href="{repository_url}/commit/{html.escape(group['commit'], quote=True)}"><code>{html.escape(group['commit'][:8])}</code> on GitHub</a>
            </div>
          </header>
          <div class="change-group-entries">{entries}</div>
        </li>"""
        )
    history_html = (
        f'<ol class="change-history">{"".join(groups)}</ol>'
        if groups
        else '<div class="empty-state"><h2>No recorded changes</h2><p>Git history was unavailable when this site was generated.</p></div>'
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="Newly published versions and manifest updates in the {catalog_label}.">
    <meta name="theme-color" content="#17213d">
    <title>Index changes · {catalog_label}</title>
    <link rel="canonical" href="{catalog['canonical_url']}changes/">
    <link rel="icon" href="../flyology-logo.svg" type="image/svg+xml">
    <link rel="stylesheet" href="../assets/styles/site.css">
    <link rel="stylesheet" href="../assets/styles/index.css?v={INDEX_CSS_VERSION}">
    <script src="../assets/scripts/ada-highlight.js"></script>
    <script src="../assets/scripts/site.js"></script>
  </head>
  <body>
    <a class="skip-link" href="#main">Skip to change history</a>
    {render_site_header('../', 'changes', catalog)}
    <main class="changes-page page-shell" id="main">
      <nav class="breadcrumbs" aria-label="Breadcrumb"><a href="../">Index</a><span aria-hidden="true">/</span><span aria-current="page">Changes</span></nav>
      <header class="changes-hero">
        <p class="eyebrow">Index activity</p>
        <h1>Index changes.</h1>
        <p>Crates and versions added or updated, derived from the repository history for packages currently in the index.</p>
      </header>
      <div class="change-stats" aria-label="Change history summary">
        <p><strong>{change_count}</strong> manifest changes</p>
        <p><strong>{published_count}</strong> new versions</p>
        <p><strong>{development_count}</strong> development updates</p>
      </div>
      {history_html}
    </main>
    <footer class="site-footer">
      <div class="footer-inner">
        <p>Generated from the <a href="{repository_url}">{catalog_label}</a>.</p>
        <div class="footer-links"><a href="{other_catalog_href('../', catalog_name)}">{"Flyology index" if catalog_name == "community" else "Community shadow"}</a><a href="../">Package index</a><a href="../crates.json">Aggregate JSON</a></div>
      </div>
    </footer>
  </body>
</html>
"""


def render_detail_footer(
    root_prefix: str, package: dict[str, Any], catalog: dict[str, Any]
) -> str:
    catalog_name = catalog["catalog"]["name"]
    repository_url = catalog["catalog"]["repository_url"]
    catalog_label = "Alire community index" if catalog_name == "community" else "Flyology Alire index"
    return f"""
    <footer class="site-footer">
      <div class="footer-inner">
        <p>Generated from the <a href="{repository_url}">{catalog_label}</a>.</p>
        <div class="footer-links"><a href="{other_catalog_href(root_prefix, catalog_name)}">{"Flyology index" if catalog_name == "community" else "Community shadow"}</a><a href="{root_prefix}">Package index</a><a href="{root_prefix}crates/{segment(package['name'])}.json">Package JSON</a></div>
      </div>
    </footer>"""


def render_source_document(
    document: SourceDocument,
    *,
    identifier: str,
    eyebrow: str,
    title: str,
    collapsible: bool = False,
    remainder: SourceDocument | None = None,
) -> str:
    source_url = source_browser_url(document)
    source_link = (
        f'<a href="{html.escape(source_url, quote=True)}">View source</a>'
        if source_url
        else ""
    )
    rendered = render_source_markdown(document, anchor_prefix=identifier)
    if collapsible:
        more = ""
        if remainder is not None:
            remainder_html = render_source_markdown(
                remainder, anchor_prefix=f"{identifier}-more"
            )
            more = f"""
            <details class="changelog-more">
              <summary>
                <span class="changelog-more-closed">See more</span>
                <span class="changelog-more-open">Show less</span>
                <span class="changelog-more-icon" aria-hidden="true"></span>
              </summary>
              <div class="changelog-more-body">{remainder_html}</div>
            </details>"""
        return f"""
      <details class="source-document source-document-collapsible" aria-labelledby="{identifier}" open>
        <summary class="source-document-summary">
          <span>
            <span class="eyebrow">{html.escape(eyebrow)}</span>
            <span class="source-document-title" id="{identifier}">{title}</span>
          </span>
          <span class="source-document-toggle" aria-hidden="true">
            <span class="source-document-toggle-open">Collapse</span>
            <span class="source-document-toggle-closed">Expand</span>
            <span class="source-document-toggle-icon"></span>
          </span>
        </summary>
        <div class="source-document-collapsible-body">
          <div class="source-document-source-link">{source_link}</div>
          <div class="markdown-body">{rendered}{more}</div>
        </div>
      </details>"""
    return f"""
      <section class="source-document" aria-labelledby="{identifier}">
        <header class="source-document-heading">
          <div>
            <p class="eyebrow">{html.escape(eyebrow)}</p>
            <h2 id="{identifier}">{title}</h2>
          </div>
          {source_link}
        </header>
        <div class="markdown-body">{rendered}</div>
      </section>"""


def render_source_documents(
    documents: ReleaseDocuments,
    *,
    package_name: str,
    version: str,
) -> str:
    sections = []
    identity = f"{segment(package_name)}-{segment(version)}"
    if documents.changelog is not None:
        sections.append(
            render_source_document(
                documents.changelog,
                identifier=f"release-notes-{identity}",
                eyebrow="Changelog excerpt",
                title=f'Release notes <code>{html.escape(version)}</code>',
                collapsible=True,
                remainder=documents.changelog_remainder,
            )
        )
    if documents.readme is not None:
        sections.append(
            render_source_document(
                documents.readme,
                identifier=f"readme-{identity}",
                eyebrow="Source documentation",
                title="README",
            )
        )
    if not sections:
        return ""
    return f'<div class="source-documents">{"".join(sections)}</div>'


def render_detail_page(
    package: dict[str, Any],
    release: dict[str, Any],
    catalog: dict[str, Any],
    *,
    page_kind: str,
    documents: ReleaseDocuments = EMPTY_RELEASE_DOCUMENTS,
) -> str:
    is_version_page = page_kind == "version"
    root_prefix = "../../../" if is_version_page else "../../"
    name = package["name"]
    version = release["version"]
    catalog_name = catalog["catalog"]["name"]
    catalog_label = "Community Crates" if catalog_name == "community" else "Flyology Crates"
    encoded_name = segment(name)
    encoded_version = segment(version)
    canonical = (
        f"{catalog['canonical_url']}crates/{encoded_name}/{encoded_version}/"
        if is_version_page
        else f"{catalog['canonical_url']}crates/{encoded_name}/"
    )
    description = release["manifest"].get("description", "No description provided.")
    kind = package_kind(release["manifest"])
    if is_version_page:
        breadcrumbs = f'<a href="{root_prefix}">Index</a><span aria-hidden="true">/</span><a href="../">{html.escape(name)}</a><span aria-hidden="true">/</span><span aria-current="page">{html.escape(version)}</span>'
        package_action = '<a class="button button-secondary" href="../">All crate versions</a>'
        json_href = f"../../{encoded_name}.json"
        manifest_href = "manifest.json"
        version_context = "version"
    else:
        breadcrumbs = f'<a href="{root_prefix}">Index</a><span aria-hidden="true">/</span><span aria-current="page">{html.escape(name)}</span>'
        package_action = f'<a class="button button-secondary" href="{encoded_version}/">Version page</a>'
        json_href = f"../{encoded_name}.json"
        manifest_href = f"{encoded_version}/manifest.json"
        version_context = "package"
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="{html.escape(description, quote=True)}">
    <meta name="theme-color" content="#17213d">
    <title>{html.escape(name)} {html.escape(version)} · {catalog_label}</title>
    <link rel="canonical" href="{canonical}">
    <link rel="alternate" type="application/json" href="{json_href}">
    <link rel="icon" href="{root_prefix}flyology-logo.svg" type="image/svg+xml">
    <link rel="stylesheet" href="{root_prefix}assets/styles/site.css">
    <link rel="stylesheet" href="{root_prefix}assets/styles/index.css?v={INDEX_CSS_VERSION}">
    <script src="{root_prefix}assets/scripts/ada-highlight.js"></script>
    <script src="{root_prefix}assets/scripts/site.js"></script>
  </head>
  <body>
    <a class="skip-link" href="#main">Skip to crate details</a>
    {render_detail_header(root_prefix, catalog)}
    <main class="crate-page page-shell" id="main">
      <nav class="breadcrumbs" aria-label="Breadcrumb">{breadcrumbs}</nav>
      <header class="crate-hero">
        <p class="eyebrow">{html.escape(kind)}</p>
        <h1>{html.escape(name)}</h1>
        <div class="crate-release-line"><code>{html.escape(version)}</code>{status_badge(release, development_only=package['development_only'])}</div>
        <p>{html.escape(description)}</p>
        <div class="actions">
          {package_action}
          <a class="button button-secondary" href="{json_href}" download>Download JSON</a>
        </div>
      </header>
      <div class="detail-page-layout">
        {render_release_detail(name, release, heading_level=2, raw_expanded=False, root_prefix=root_prefix, current_catalog=catalog_name, include_relationships=False, include_raw_manifest=False)}
        {render_detail_rail(package, release, context=version_context, root_prefix=root_prefix, manifest_href=manifest_href, current_catalog=catalog_name)}
      </div>
      {render_source_documents(documents, package_name=name, version=version)}
    </main>
    {render_detail_footer(root_prefix, package, catalog)}
  </body>
</html>
"""


INDEX_CSS = r"""
.brand-mark { display: block; width: 2rem; height: 2rem; }
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
.changes-preview { padding-block: clamp(4.5rem, 8vw, 7rem); border-bottom: 1px solid var(--line); }
.changes-heading { display: grid; grid-template-columns: minmax(0, .9fr) minmax(18rem, .55fr); align-items: end; margin-bottom: 2.5rem; gap: 3rem; }
.changes-heading h2 { max-width: 12ch; margin-bottom: 0; }
.changes-heading > p { max-width: 48ch; margin: 0 0 .3rem; color: var(--ink-soft); }
.change-preview-list, .change-history { margin: 0; padding: 0; border-top: 1px solid var(--line); list-style: none; }
.change-preview-list > li { display: grid; grid-template-columns: 8rem minmax(0, 1fr); align-items: center; border-bottom: 1px solid var(--line); }
.change-preview-list > li > time, .change-group-heading > time { color: var(--ink-soft); font: .7rem var(--font-mono); white-space: nowrap; }
.change-preview-list .change-entry { display: grid; grid-template-columns: minmax(16rem, .72fr) minmax(12rem, 1fr); align-items: center; min-height: 5rem; padding: .9rem .2rem; gap: 1.4rem; }
.change-entry-heading { display: flex; flex-wrap: wrap; align-items: center; gap: .55rem .8rem; }
.change-entry-heading h3 { margin: 0; font-size: .9rem; letter-spacing: -.015em; }
.change-entry-heading h3 a { color: var(--ink); text-decoration: none; }
.change-entry-heading h3 a:hover { color: var(--violet-deep); }
.change-entry-heading h3 code { margin-left: .25rem; color: var(--violet-deep); font-size: .7rem; font-weight: 400; }
.change-kind { display: inline-flex; align-items: center; padding: .17rem .46rem; border: 1px solid currentColor; border-radius: 999px; font-size: .6rem; font-weight: 650; letter-spacing: .025em; white-space: nowrap; }
.change-entry-package .change-kind, .change-entry-published .change-kind { background: color-mix(in oklch, var(--teal) 10%, var(--paper)); color: var(--teal-deep); }
.change-entry-development .change-kind { background: color-mix(in oklch, var(--violet) 8%, var(--paper)); color: var(--violet-deep); }
.change-entry-manifest .change-kind { background: var(--surface); color: var(--ink-soft); }
.change-entry-summary { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; color: var(--ink-soft); font-size: .76rem; }
.change-entry-summary code { color: var(--ink); font-size: .68rem; }
.text-link { display: inline-flex; align-items: center; margin-top: 1.5rem; gap: .5rem; font-size: .8rem; font-weight: 620; }
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
.nav-links a.catalog-switch { display: inline-flex; align-items: center; padding: .38rem .68rem; border: 1px solid var(--line); border-radius: 999px; gap: .45rem; background: var(--surface); color: var(--ink); line-height: 1; text-decoration: none; }
.nav-links a.catalog-switch:hover { border-color: var(--violet); background: var(--surface-strong); color: var(--violet-deep); }
.catalog-switch span:last-child { color: var(--violet-deep); font-size: .7em; }
.package { border-bottom: 1px solid var(--line); }
.package[hidden] { display: none; }
.package-summary { display: grid; grid-template-columns: minmax(13rem, .72fr) minmax(16rem, 1.2fr) auto; align-items: center; min-height: 7.2rem; padding: 1.2rem .25rem; gap: 1.4rem; cursor: pointer; list-style: none; transition: background-color 180ms var(--ease-out), padding 180ms var(--ease-out); }
.package-summary::-webkit-details-marker, .raw-manifest > summary::-webkit-details-marker { display: none; }
.package-summary:hover, .package[open] > .package-summary { padding-inline: 1rem; background: var(--surface); }
.package-identity { display: flex; flex-wrap: wrap; align-items: baseline; gap: .65rem; min-width: 0; }
.package-name { overflow-wrap: anywhere; font-size: clamp(1.05rem, 2vw, 1.4rem); font-weight: 620; letter-spacing: -.025em; }
.version-badge { color: var(--violet-deep); font: .72rem var(--font-mono); }
.release-status { display: inline-flex; align-items: center; padding: .18rem .48rem; border: 1px solid currentColor; border-radius: 999px; font: 620 .62rem var(--font-sans); letter-spacing: .035em; white-space: nowrap; }
.release-status-development { background: color-mix(in oklch, var(--violet) 8%, var(--paper)); color: var(--violet-deep); }
.package-description { max-width: 58ch; color: var(--ink-soft); font-size: .92rem; }
.summary-action { display: inline-flex; align-items: center; gap: .6rem; color: var(--ink-soft); font-size: .72rem; }
.summary-action span { display: grid; width: 2rem; height: 2rem; place-items: center; border: 1px solid var(--line); border-radius: 50%; color: var(--ink); font-size: 1rem; transition: transform 200ms var(--ease-out); }
.package[open] > summary .summary-action span { transform: rotate(45deg); }
.package-body { padding: 0 1rem 2rem; }
.package-facts { display: flex; flex-wrap: wrap; align-items: center; gap: .65rem 1rem; padding: 1rem 0 1.5rem; border-bottom: 1px solid var(--line); }
.kind-label { color: var(--teal-deep); font: 650 .7rem var(--font-mono); text-transform: uppercase; letter-spacing: .06em; }
.tag-list { display: flex; flex-wrap: wrap; margin: 0; padding: 0; gap: .35rem; list-style: none; }
.tag-list li { padding: .16rem .48rem; border: 1px solid var(--line); border-radius: 999px; color: var(--ink-soft); font-size: .68rem; }
.package-links { display: flex; flex-wrap: wrap; margin-left: auto; gap: .45rem 1rem; font-size: .76rem; font-weight: 580; }
.package-release-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(14rem, .32fr); align-items: start; gap: clamp(1.5rem, 4vw, 3rem); padding-top: 1.8rem; }
.release-detail { min-width: 0; }
.release-heading { display: flex; flex-wrap: wrap; align-items: center; margin-bottom: 1rem; gap: .7rem; }
.release-heading h2, .release-heading h3 { margin: 0; font-size: 1.15rem; letter-spacing: -.018em; }
.metadata { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; border-top: 1px solid var(--line); }
.metadata div { display: grid; grid-template-columns: 8rem 1fr; padding: .75rem .2rem; gap: 1rem; border-bottom: 1px solid var(--line); }
.metadata div:nth-child(odd) { margin-right: 1.2rem; }
.metadata dt { color: var(--ink-soft); font-size: .7rem; }
.metadata dd { min-width: 0; margin: 0; overflow-wrap: anywhere; font: .72rem/1.55 var(--font-mono); }
.detail-section { margin-top: 1.6rem; }
.detail-section h4 { margin-bottom: .7rem; font-size: .82rem; }
.quiet { color: var(--ink-soft); font-size: .8rem; }
.dependency-list { display: grid; margin: 0; padding: 0; gap: .45rem; list-style: none; }
.dependency-list li { background: var(--surface); font-size: .76rem; }
.dependency-row { display: grid; grid-template-columns: minmax(10rem, .3fr) minmax(0, 1fr); padding: .55rem .7rem; gap: 1rem; }
.dependency-identity { display: grid; min-width: 0; gap: .1rem; }
.dependency-identity code { color: var(--ink); overflow-wrap: anywhere; }
.dependency-identity small, .dependency-condition { color: var(--ink-soft); font: 600 .58rem var(--font-sans); letter-spacing: .025em; }
.dependency-requirement { display: grid; align-content: center; min-width: 0; overflow-wrap: anywhere; }
.dependency-condition { display: block; margin-top: .15rem; }
.dependency-link { color: var(--ink); text-decoration: none; }
.dependency-link:hover { background: var(--surface-strong); }
.dependency-link:hover code { color: var(--violet-deep); }
.dependency-list span { color: var(--ink-soft); font-family: var(--font-mono); }
.dependant-summary { margin: 0 0 .7rem; }
.dependant-list { display: grid; margin: 0; padding: 0; gap: .45rem; list-style: none; }
.dependant-group { padding: .6rem .7rem; background: var(--surface); }
.dependant-name { display: inline-block; font-size: .76rem; font-weight: 620; }
.dependant-catalog { margin-left: .45rem; color: var(--ink-soft); font-size: .58rem; font-weight: 600; letter-spacing: .025em; }
.dependant-releases { display: grid; margin: .45rem 0 0; padding: 0; gap: .3rem; list-style: none; }
.dependant-release { display: grid; grid-template-columns: minmax(7rem, .28fr) minmax(0, 1fr) 5.4rem; align-items: baseline; gap: .9rem; font-size: .74rem; }
.dependant-release > a { color: var(--ink); font-family: var(--font-mono); overflow-wrap: anywhere; text-decoration: none; }
.dependant-release > a:hover { color: var(--violet-deep); text-decoration: underline; }
.dependant-release code { color: var(--ink-soft); font-size: .7rem; overflow-wrap: anywhere; }
.dependant-verdict { justify-self: end; color: var(--ink-soft); font: 620 .62rem var(--font-sans); letter-spacing: .035em; white-space: nowrap; }
.dependant-release-qualifies .dependant-verdict { color: var(--teal-deep); }
.dependant-release-system .dependant-verdict { color: var(--violet-deep); }
.raw-manifest { margin-top: 1.5rem; }
.raw-manifest > summary { display: inline-flex; padding: .6rem .8rem; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--surface); font-size: .76rem; font-weight: 600; cursor: pointer; list-style: none; }
.raw-manifest pre { max-height: 32rem; margin: .7rem 0 0; padding: 1rem; overflow: auto; border: 1px solid var(--code-line); border-radius: var(--radius-md); background: var(--code-bg); color: oklch(91% .02 270); font-size: .72rem; line-height: 1.65; }
.version-index { min-width: 0; padding-top: .15rem; }
.version-index h3 { margin-bottom: .7rem; font-size: .82rem; letter-spacing: -.01em; }
.version-links { margin: 0; padding: 0; border-top: 1px solid var(--line); list-style: none; }
.version-links li { border-bottom: 1px solid var(--line); }
.version-links a { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; padding: .72rem .15rem; gap: .6rem; font: .7rem var(--font-mono); text-decoration: none; }
.version-links a:hover { background: var(--surface); }
.version-links a > span:first-child { overflow-wrap: anywhere; }
.version-link-status { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: .35rem; }
.current-label { color: var(--ink-soft); font: 600 .6rem var(--font-sans); white-space: nowrap; }
.breadcrumbs { display: flex; flex-wrap: wrap; padding-top: 2rem; gap: .45rem; color: var(--ink-soft); font-size: .72rem; }
.breadcrumbs a { color: var(--ink-soft); }
.crate-page { padding-bottom: clamp(5rem, 10vw, 8rem); }
.crate-hero { max-width: 58rem; padding-block: clamp(4rem, 8vw, 7rem); }
.crate-hero h1 { max-width: 100%; margin-bottom: 1rem; overflow-wrap: anywhere; font-size: clamp(2.8rem, 7vw, 6rem); }
.crate-hero > p:not(.eyebrow) { max-width: 62ch; margin: 1.3rem 0 1.8rem; color: var(--ink-soft); font-size: 1.05rem; }
.crate-release-line { display: flex; flex-wrap: wrap; align-items: center; gap: .7rem; }
.crate-release-line code { color: var(--violet-deep); font-size: .9rem; }
.detail-page-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(20rem, .46fr); align-items: start; gap: clamp(2rem, 6vw, 5rem); padding-top: 2rem; border-top: 1px solid var(--line); }
.detail-page-rail { display: grid; min-width: 0; gap: 2rem; }
.detail-rail-section { min-width: 0; }
.detail-rail-section h3 { margin-bottom: .7rem; font-size: .82rem; letter-spacing: -.01em; }
.detail-page-rail .dependency-row { grid-template-columns: minmax(7rem, .7fr) minmax(0, 1fr); }
.detail-page-rail .dependant-release { grid-template-columns: minmax(5.5rem, .5fr) minmax(0, 1fr) 4.8rem; gap: .6rem; }
.manifest-json-link { display: flex; justify-content: space-between; align-items: center; padding: .8rem .15rem; border-block: 1px solid var(--line); gap: 1rem; font-size: .76rem; font-weight: 620; text-decoration: none; }
.manifest-json-link::after { content: "↓"; color: var(--ink-soft); font-size: .9rem; }
.manifest-json-link:hover { background: var(--surface); }
.source-documents { margin-top: clamp(4rem, 9vw, 7rem); border-top: 1px solid var(--line); }
.source-document { display: grid; grid-template-columns: minmax(11rem, .3fr) minmax(0, 1fr); padding-block: clamp(2.5rem, 6vw, 4.5rem); gap: clamp(2rem, 7vw, 6rem); border-bottom: 1px solid var(--line); }
.source-document-heading { align-self: start; }
.source-document-heading h2 { margin: .35rem 0 .8rem; font-size: 1.35rem; letter-spacing: -.025em; }
.source-document-heading h2 code { display: block; margin-top: .4rem; color: var(--violet-deep); font-size: .72rem; font-weight: 400; overflow-wrap: anywhere; }
.source-document-heading > a { font-size: .72rem; font-weight: 620; }
.source-document-collapsible { display: block; padding-block: 0; }
.source-document-summary { display: grid; grid-template-columns: minmax(11rem, .3fr) minmax(0, 1fr); align-items: center; padding-block: clamp(2.5rem, 6vw, 4.5rem); gap: clamp(2rem, 7vw, 6rem); cursor: pointer; list-style: none; }
.source-document-summary::-webkit-details-marker { display: none; }
.source-document-summary .eyebrow { display: block; }
.source-document-title { display: block; margin-top: .35rem; font-size: 1.35rem; font-weight: 620; letter-spacing: -.025em; }
.source-document-title code { display: block; margin-top: .4rem; color: var(--violet-deep); font-size: .72rem; font-weight: 400; overflow-wrap: anywhere; }
.source-document-toggle { display: inline-flex; justify-self: end; align-items: center; gap: .55rem; color: var(--ink-soft); font-size: .7rem; font-weight: 620; }
.source-document-toggle-icon { display: grid; width: 1.8rem; height: 1.8rem; place-items: center; border: 1px solid var(--line); border-radius: 50%; color: var(--ink); }
.source-document-toggle-icon::before { content: "+"; font-size: 1rem; line-height: 1; }
.source-document-collapsible[open] .source-document-toggle-icon::before { content: "−"; }
.source-document-collapsible[open] .source-document-toggle-closed, .source-document-collapsible:not([open]) .source-document-toggle-open { display: none; }
.source-document-summary:hover .source-document-title { color: var(--violet-deep); }
.source-document-summary:hover .source-document-toggle-icon { background: var(--surface); }
.source-document-summary:focus-visible { outline: 2px solid var(--focus); outline-offset: .35rem; }
.source-document-collapsible-body { display: grid; grid-template-columns: minmax(11rem, .3fr) minmax(0, 1fr); padding-bottom: clamp(2.5rem, 6vw, 4.5rem); gap: clamp(2rem, 7vw, 6rem); }
.source-document-source-link { font-size: .72rem; font-weight: 620; }
.changelog-more { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--line); }
.changelog-more > summary { display: inline-flex; align-items: center; gap: .55rem; color: var(--violet-deep); font-size: .76rem; font-weight: 620; cursor: pointer; list-style: none; }
.changelog-more > summary::-webkit-details-marker { display: none; }
.changelog-more-icon { display: grid; width: 1.55rem; height: 1.55rem; place-items: center; border: 1px solid var(--line); border-radius: 50%; color: var(--ink); }
.changelog-more-icon::before { content: "+"; font-size: .9rem; line-height: 1; }
.changelog-more[open] .changelog-more-icon::before { content: "−"; }
.changelog-more[open] .changelog-more-closed, .changelog-more:not([open]) .changelog-more-open { display: none; }
.changelog-more > summary:hover { color: var(--violet); }
.changelog-more > summary:hover .changelog-more-icon { background: var(--surface); }
.changelog-more > summary:focus-visible { outline: 2px solid var(--focus); outline-offset: .3rem; }
.changelog-more-body { margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid var(--line); }
.markdown-body { min-width: 0; max-width: 74ch; color: var(--ink); overflow-wrap: anywhere; }
.markdown-body > :first-child { margin-top: 0; }
.markdown-body > :last-child { margin-bottom: 0; }
.markdown-body h3, .markdown-body h4, .markdown-body h5, .markdown-body h6 { max-width: 100%; margin: 2.2rem 0 .75rem; letter-spacing: -.018em; }
.markdown-body h3 { font-size: 1.35rem; }
.markdown-body h4 { font-size: 1.08rem; }
.markdown-body h5, .markdown-body h6 { font-size: .9rem; }
.markdown-body p, .markdown-body li { font-size: .94rem; line-height: 1.72; }
.markdown-body ul, .markdown-body ol { padding-left: 1.4rem; }
.markdown-body li + li { margin-top: .32rem; }
.markdown-body a { text-underline-offset: .16em; }
.markdown-body img { display: block; max-width: 100%; height: auto; margin-block: 1.5rem; border-radius: var(--radius-sm); }
.markdown-body blockquote { margin: 1.5rem 0; padding: .85rem 1rem; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--surface); color: var(--ink-soft); }
.markdown-body blockquote > :first-child { margin-top: 0; }
.markdown-body blockquote > :last-child { margin-bottom: 0; }
.markdown-body code { padding: .12em .32em; border-radius: .25rem; background: var(--surface-strong); font-size: .82em; }
.markdown-body pre { max-width: 100%; padding: 1rem; overflow: auto; border: 1px solid var(--code-line); border-radius: var(--radius-md); background: var(--code-bg); color: oklch(91% .02 270); font-size: .76rem; line-height: 1.65; }
.markdown-body pre code { padding: 0; background: transparent; color: inherit; font-size: inherit; }
.markdown-body table { display: block; max-width: 100%; margin-block: 1.5rem; overflow-x: auto; border-collapse: collapse; font-size: .82rem; }
.markdown-body th, .markdown-body td { padding: .55rem .7rem; border: 1px solid var(--line); text-align: left; vertical-align: top; }
.markdown-body th { background: var(--surface); font-weight: 620; }
.markdown-body hr { margin-block: 2.5rem; border: 0; border-top: 1px solid var(--line); }
.changes-page { padding-bottom: clamp(5rem, 10vw, 8rem); }
.changes-hero { max-width: 58rem; padding-block: clamp(4rem, 8vw, 7rem); }
.changes-hero h1 { max-width: 100%; margin-bottom: 1rem; font-size: clamp(2.8rem, 7vw, 6rem); }
.changes-hero > p:not(.eyebrow) { max-width: 64ch; margin: 0; color: var(--ink-soft); font-size: 1.05rem; }
.change-stats { display: grid; grid-template-columns: repeat(3, 1fr); border-block: 1px solid var(--line); }
.change-stats p { margin: 0; padding: 1.1rem 1.5rem; border-right: 1px solid var(--line); color: var(--ink-soft); font-size: .74rem; }
.change-stats p:first-child { padding-left: 0; }
.change-stats p:last-child { border-right: 0; }
.change-stats strong { display: block; color: var(--ink); font: 620 1rem var(--font-mono); }
.change-history { margin-top: 4rem; }
.change-group { padding-block: 2.2rem 3rem; border-bottom: 1px solid var(--line); }
.change-group-heading { display: grid; grid-template-columns: 8rem minmax(0, 1fr); align-items: start; gap: 2rem; }
.change-group-heading h2 { max-width: 34ch; margin: 0 0 .4rem; font-size: 1.25rem; letter-spacing: -.02em; }
.change-group-heading a { font-size: .68rem; }
.change-group-entries { margin: 1.8rem 0 0 10rem; border-top: 1px solid var(--line); }
.change-group-entries .change-entry { padding-block: 1.4rem; border-bottom: 1px solid var(--line); }
.change-group-entries .change-entry:last-child { border-bottom: 0; }
.change-group-entries .change-entry-heading { margin-bottom: .5rem; }
.change-description { max-width: 68ch; margin: .75rem 0 1rem; color: var(--ink-soft); font-size: .84rem; }
.change-facts { display: grid; margin: 0; border-top: 1px solid var(--line); }
.change-facts > div { display: grid; grid-template-columns: 8.5rem minmax(0, 1fr); padding: .65rem .15rem; gap: 1rem; border-bottom: 1px solid var(--line); }
.change-facts dt { color: var(--ink-soft); font-size: .68rem; }
.change-facts dd { min-width: 0; margin: 0; overflow-wrap: anywhere; font: .7rem/1.55 var(--font-mono); }
.change-revisions { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; }
.change-dependencies { margin-top: 1.2rem; }
.change-dependencies h4 { margin-bottom: .55rem; font-size: .75rem; }
.change-dependencies ul { display: grid; margin: 0; padding: 0; gap: .35rem; list-style: none; }
.change-dependencies li { display: flex; flex-wrap: wrap; align-items: center; padding: .45rem .65rem; gap: .35rem; background: var(--surface); color: var(--ink-soft); font-size: .72rem; }
.change-dependencies li code:first-child { color: var(--ink); }
.change-compare { display: inline-flex; margin-top: 1rem; font-size: .75rem; font-weight: 620; }
.empty-state { padding: 4rem 1rem; border-bottom: 1px solid var(--line); text-align: center; }
.empty-state h3 { margin-bottom: .5rem; }
.empty-state p { color: var(--ink-soft); }
@media (max-width: 900px) {
  .catalog-hero { grid-template-columns: 1fr; min-height: 0; }
  .install-panel { max-width: 38rem; }
  .catalog-heading { grid-template-columns: 1fr; gap: 1.2rem; }
  .changes-heading { grid-template-columns: 1fr; gap: 1.2rem; }
  .change-preview-list .change-entry { grid-template-columns: 1fr; gap: .5rem; }
  .catalog-controls { grid-template-columns: 1fr 1fr; }
  .expand-button { grid-column: 1 / -1; }
  .package-summary { grid-template-columns: 1fr auto; }
  .package-description { grid-column: 1 / -1; grid-row: 2; }
  .summary-action { grid-column: 2; grid-row: 1; }
  .package-release-layout, .detail-page-layout { grid-template-columns: 1fr; }
  .source-document { grid-template-columns: 1fr; gap: 1.5rem; }
  .source-document-heading { display: flex; justify-content: space-between; align-items: end; gap: 1rem; }
  .source-document-collapsible { display: block; }
  .source-document-summary { grid-template-columns: minmax(0, 1fr) auto; gap: 1rem; }
  .source-document-collapsible-body { grid-template-columns: 1fr; gap: 1rem; }
  .metadata { grid-template-columns: 1fr; }
  .metadata div:nth-child(odd) { margin-right: 0; }
  .change-group-entries { margin-left: 0; }
}
@media (max-width: 640px) {
  .catalog-stats .page-shell { grid-template-columns: repeat(2, 1fr); }
  .catalog-stats p:nth-child(2) { border-right: 0; }
  .catalog-stats p:nth-child(-n+2) { border-bottom: 1px solid var(--line); }
  .catalog-stats p:nth-child(3) { padding-left: 0; }
  .catalog-controls { grid-template-columns: 1fr; }
  .change-preview-list > li, .change-group-heading { grid-template-columns: 1fr; gap: .65rem; }
  .change-preview-list > li { padding-block: 1rem; }
  .change-preview-list .change-entry { min-height: 0; padding-block: 0; }
  .change-stats { grid-template-columns: 1fr; }
  .change-stats p, .change-stats p:first-child { padding-inline: 0; border-right: 0; border-bottom: 1px solid var(--line); }
  .change-stats p:last-child { border-bottom: 0; }
  .change-facts > div { grid-template-columns: 1fr; gap: .3rem; }
  .expand-button { grid-column: auto; }
  .package-summary { min-height: 0; padding-block: 1.4rem; }
  .package-body { padding-inline: .35rem; }
  .package-links { width: 100%; margin-left: 0; }
  .metadata div, .dependency-row { grid-template-columns: 1fr; gap: .3rem; }
  .dependant-releases { gap: .55rem; }
  .dependant-release { grid-template-columns: minmax(0, 1fr) auto; gap: .15rem .8rem; }
  .dependant-release > a { grid-area: 1 / 1; }
  .dependant-release .dependant-verdict { grid-area: 1 / 2; }
  .dependant-release code { grid-area: 2 / 1 / 3 / -1; }
  .version-links a { grid-template-columns: 1fr; }
  .version-link-status { justify-content: flex-start; }
}
@media (prefers-reduced-motion: reduce) {
  .package-summary, .summary-action span { transition: none; }
}
"""

INDEX_CSS_VERSION = hashlib.sha256(INDEX_CSS.encode()).hexdigest()[:12]


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


README_TEXT = """Alire crate index JSON
======================

crates.json is the aggregate catalog. Its schema_version is currently 4.
Each crates/<name>.json file contains the same package object plus the catalog
schema_version, generated_at, canonical_url, catalog provenance, and index
metadata. The catalog revision is the exact Git commit rendered by this build.

Each package records latest_version, selected_version, and development_only.
selected_version is the newest non-dev release when one exists; otherwise it
is the newest development release.

Every version has a path and manifest field. The manifest is a lossless JSON
representation of the corresponding TOML manifest, subject only to TOML date
and time values being represented as ISO 8601 strings.

The same manifest object is also available directly at
crates/<name>/<version>/manifest.json.

Every version has a dependencies array resolving each declared version set
against the ordered Flyology and community catalogs. It records conditional
branches, the catalog and package supplying the dependency, the selected
release, and the version exposed through provides. A system external resolves
to the synthetic system release because its concrete version is detected on
the user's host rather than known at site-build time.

Every version also has a dependants array listing the indexed releases that
depend on it, grouped by crate name and ordered newest version first. Each
entry records the dependant's name, version, development flag, path, and
whether that version is its crate's selected one. The requires field names the
crate the dependant asks for and provided_version is the version this release
carries under that name, so a toolchain declaring provides = ["gnat=16.2.0"] is
resolved as gnat 16.2.0. The requirement field is the declared version set and
qualifies reports whether provided_version satisfies it, following Alire's
semantic_versioning rules. A null qualifies value identifies a system external
whose installed version cannot be known by the static catalog.

A version or version set the generator cannot parse fails site generation
rather than producing a partial answer.

Browser clients on other origins can fetch these files because GitHub Pages
serves static assets with Access-Control-Allow-Origin: *.
"""


def render_llms(catalog: dict[str, Any]) -> str:
    canonical_url = catalog["canonical_url"]
    catalog_name = catalog["catalog"]["name"]
    repository_url = catalog["catalog"]["repository_url"]
    title = (
        "Alire Community Index Shadow"
        if catalog_name == "community"
        else "Flyology Crate Index"
    )
    summary = (
        "A daily generated human- and machine-readable shadow of the Alire community index."
        if catalog_name == "community"
        else "A human- and machine-readable catalog of Ada packages and GNAT compiler builds maintained by Flyology and published through a custom Alire index."
    )
    package_links = []
    for package in catalog["packages"]:
        name = package["name"]
        description = package["description"].rstrip()
        if not description.endswith((".", "!", "?")):
            description += "."
        status = " Development-only." if package["development_only"] else ""
        package_links.append(
            f"- [{name}]({canonical_url}crates/{segment(name)}.json): "
            f"{description} Selected version: {package['selected_version']}.{status}"
        )

    return f"""# {title}

> {summary}

The JSON resources are generated directly from indexed TOML manifests and preserve every manifest field. Every dependency records the catalog and highest matching release selected using Alire version-set semantics.

## Catalog

- [Catalog home]({canonical_url}): Browse and search this Alire index.
- [Aggregate JSON catalog]({canonical_url}crates.json): Complete package and version inventory, including every parsed manifest.
- [Change history]({canonical_url}changes/): Publications and manifest updates derived from Git history.
- [JSON schema notes]({canonical_url}README.txt): Field semantics and endpoint conventions.

## Packages

{chr(10).join(package_links)}

## Optional

- [Source repository]({repository_url}): Alire manifests and index configuration.
"""


FLYOLOGY_MARK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-labelledby="title desc">
  <title id="title">Flyology transparent mark</title>
  <desc id="desc">An abstract flight mark inside an open event loop with three cooperative task nodes.</desc>
  <defs>
    <mask id="node-gaps" maskUnits="userSpaceOnUse" x="0" y="0" width="256" height="256">
      <rect width="256" height="256" fill="white"/>
      <circle cx="203.4" cy="77.1" r="10.5" fill="black"/>
      <circle cx="219" cy="128" r="10.5" fill="black"/>
      <circle cx="203.4" cy="178.9" r="10.5" fill="black"/>
    </mask>
  </defs>

  <g mask="url(#node-gaps)" fill="none" stroke-width="8" stroke-linecap="round">
    <circle cx="128" cy="128" r="91" stroke="#AAB5CF"/>
    <path d="M186.5 58.3A91 91 0 0 1 219 128" stroke="#6F66EE"/>
    <path d="M219 128A91 91 0 0 1 186.5 197.7" stroke="#24BEB5"/>
  </g>

  <circle cx="203.4" cy="77.1" r="6.5" fill="#6F66EE"/>
  <circle cx="219" cy="128" r="6.5" fill="#6F66EE"/>
  <circle cx="203.4" cy="178.9" r="6.5" fill="#24BEB5"/>
  <path d="M177.8 205.8C179.3 201.4 180.2 197.4 180.4 193.2C184 196.3 188.1 198.7 192.5 199.9C187.9 201.1 182.8 203.2 177.8 205.8Z" fill="#24BEB5"/>

  <g fill="#17213D">
    <path d="M143 103C116 88 91 78 62 75C84 94 102 112 116 134C120 140 128 138 134 132C141 124 145 113 143 103Z"/>
    <path d="M128 137C109 127 89 122 68 124C86 135 101 148 112 163C117 169 124 166 129 160C134 153 134 144 128 137Z"/>
    <path d="M119 177C126 145 133 118 146 97C158 81 173 72 191 68C176 87 160 108 148 134C140 152 133 165 119 177Z"/>
    <circle cx="129" cy="137" r="4"/>
  </g>
</svg>
"""


FLYOLOGY_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-labelledby="title desc">
  <title id="title">Flyology primary icon</title>
  <desc id="desc">An abstract flight mark inside an open event loop with three cooperative task nodes.</desc>
  <defs>
    <mask id="node-gaps" maskUnits="userSpaceOnUse" x="0" y="0" width="256" height="256">
      <rect width="256" height="256" fill="white"/>
      <circle cx="203.4" cy="77.1" r="10.5" fill="black"/>
      <circle cx="219" cy="128" r="10.5" fill="black"/>
      <circle cx="203.4" cy="178.9" r="10.5" fill="black"/>
    </mask>
  </defs>

  <rect x="12" y="12" width="232" height="232" rx="58" fill="#10172B"/>

  <g mask="url(#node-gaps)" fill="none" stroke-width="8" stroke-linecap="round">
    <circle cx="128" cy="128" r="91" stroke="#3C4868"/>
    <path d="M186.5 58.3A91 91 0 0 1 219 128" stroke="#756CF6"/>
    <path d="M219 128A91 91 0 0 1 186.5 197.7" stroke="#2CCBC1"/>
  </g>

  <circle cx="203.4" cy="77.1" r="6.5" fill="#756CF6"/>
  <circle cx="219" cy="128" r="6.5" fill="#756CF6"/>
  <circle cx="203.4" cy="178.9" r="6.5" fill="#2CCBC1"/>
  <path d="M177.8 205.8C179.3 201.4 180.2 197.4 180.4 193.2C184 196.3 188.1 198.7 192.5 199.9C187.9 201.1 182.8 203.2 177.8 205.8Z" fill="#2CCBC1"/>

  <g fill="#F7F9FF">
    <path d="M143 103C116 88 91 78 62 75C84 94 102 112 116 134C120 140 128 138 134 132C141 124 145 113 143 103Z"/>
    <path d="M128 137C109 127 89 122 68 124C86 135 101 148 112 163C117 169 124 166 129 160C134 153 134 144 128 137Z"/>
    <path d="M119 177C126 145 133 118 146 97C158 81 173 72 191 68C176 87 160 108 148 134C140 152 133 165 119 177Z"/>
    <circle cx="129" cy="137" r="4"/>
  </g>
</svg>
"""


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_catalog_site(
    catalog: dict[str, Any],
    history: list[dict[str, Any]],
    output: Path,
    release_documents: dict[str, ReleaseDocuments] | None = None,
) -> None:
    documents = release_documents or {}
    (output / "assets" / "styles").mkdir(parents=True)
    (output / "assets" / "scripts").mkdir(parents=True)
    (output / "crates").mkdir(parents=True)
    (output / "changes").mkdir(parents=True)

    write_json(output / "crates.json", catalog)
    shared = {
        key: catalog[key]
        for key in (
            "schema_version",
            "generated_at",
            "canonical_url",
            "catalog",
            "index",
        )
    }
    for package in catalog["packages"]:
        encoded_name = segment(package["name"])
        package_directory = output / "crates" / path_segment(package["name"])
        package_directory.mkdir(parents=True)
        selected = release_for(package, package["selected_version"])
        write_json(
            output / "crates" / f"{path_segment(package['name'])}.json",
            {**shared, "package": package},
        )
        (package_directory / "index.html").write_text(
            render_detail_page(
                package,
                selected,
                catalog,
                page_kind="package",
                documents=documents.get(
                    selected["path"], EMPTY_RELEASE_DOCUMENTS
                ),
            ),
            encoding="utf-8",
        )
        for release in package["versions"]:
            version_directory = package_directory / path_segment(release["version"])
            version_directory.mkdir(parents=True)
            write_json(version_directory / "manifest.json", release["manifest"])
            (version_directory / "index.html").write_text(
                render_detail_page(
                    package,
                    release,
                    catalog,
                    page_kind="version",
                    documents=documents.get(
                        release["path"], EMPTY_RELEASE_DOCUMENTS
                    ),
                ),
                encoding="utf-8",
            )

    (output / "index.html").write_text(
        render_html(catalog, history), encoding="utf-8"
    )
    (output / "changes" / "index.html").write_text(
        render_changes_page(catalog, history), encoding="utf-8"
    )
    (output / "assets" / "styles" / "index.css").write_text(
        INDEX_CSS.strip() + "\n", encoding="utf-8"
    )
    (output / "assets" / "scripts" / "index.js").write_text(
        INDEX_JS.strip() + "\n", encoding="utf-8"
    )
    (output / "flyology-mark.svg").write_text(FLYOLOGY_MARK, encoding="utf-8")
    (output / "flyology-logo.svg").write_text(FLYOLOGY_LOGO, encoding="utf-8")
    (output / "README.txt").write_text(README_TEXT, encoding="utf-8")
    (output / "llms.txt").write_text(render_llms(catalog), encoding="utf-8")


def generate(
    source: Path,
    output: Path,
    *,
    include_source_documents: bool = False,
    source_cache: Path | None = None,
    community_source: Path | None = None,
) -> dict[str, Any]:
    catalog = load_catalog(source, attach_relationships=False)
    community_catalog = (
        load_catalog(
            community_source,
            catalog_name="community",
            canonical_url=COMMUNITY_CANONICAL_URL,
            repository_url=COMMUNITY_REPOSITORY_URL,
            attach_relationships=False,
        )
        if community_source is not None
        else None
    )
    relationship_packages = list(catalog["packages"])
    if community_catalog is not None:
        relationship_packages.extend(community_catalog["packages"])
    attach_dependants(relationship_packages)
    attach_resolved_dependencies(relationship_packages)

    history = load_change_history(catalog, source.parent)
    community_history = (
        load_change_history(community_catalog, community_source.parent)
        if community_catalog is not None and community_source is not None
        else []
    )
    release_documents: dict[str, ReleaseDocuments] = {}
    if include_source_documents:
        temporary_cache = (
            tempfile.TemporaryDirectory(prefix="alire-index-sources-")
            if source_cache is None
            else None
        )
        cache = source_cache or Path(temporary_cache.name)  # type: ignore[union-attr]
        try:
            loader = SourceDocumentLoader(cache)
            for package in catalog["packages"]:
                for release in package["versions"]:
                    release_documents[release["path"]] = loader.load(
                        release["manifest"]
                    )
            for package in catalog["packages"]:
                for release in package["versions"]:
                    documents = release_documents[release["path"]]
                    if documents.changelog is not None:
                        continue
                    fallback = loader.fallback_changelog(release["manifest"])
                    if fallback is not None:
                        changelog, remainder = fallback
                        release_documents[release["path"]] = documents._replace(
                            changelog=changelog,
                            changelog_remainder=remainder,
                        )
        finally:
            if temporary_cache is not None:
                temporary_cache.cleanup()
    if output.exists():
        shutil.rmtree(output)
    write_catalog_site(catalog, history, output, release_documents)
    if community_catalog is not None:
        write_catalog_site(
            community_catalog,
            community_history,
            output / "community",
        )
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "index")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "site")
    parser.add_argument(
        "--community-source",
        type=Path,
        help="also render a shadow of this checked-out community index under /community",
    )
    parser.add_argument(
        "--skip-source-documents",
        action="store_true",
        help="do not fetch and render pinned README and CHANGELOG files",
    )
    parser.add_argument(
        "--source-cache",
        type=Path,
        help="reuse bare source-repository clones from this directory",
    )
    args = parser.parse_args()
    try:
        catalog = generate(
            args.source.resolve(),
            args.output.resolve(),
            include_source_documents=not args.skip_source_documents,
            source_cache=args.source_cache.resolve() if args.source_cache else None,
            community_source=(
                args.community_source.resolve() if args.community_source else None
            ),
        )
    except (OSError, RuntimeError, tomllib.TOMLDecodeError, ValueError) as error:
        print(f"site generation failed: {error}", file=sys.stderr)
        return 1
    package_count = len(catalog["packages"])
    version_count = sum(len(package["versions"]) for package in catalog["packages"])
    scope = f"{package_count} Flyology packages and {version_count} manifests"
    if args.community_source:
        scope += " plus the community shadow"
    print(f"Generated {scope} at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
