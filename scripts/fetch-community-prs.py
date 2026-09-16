#!/usr/bin/env python3
"""Snapshot open Alire community index pull requests for the static site."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API = "https://api.github.com/repos/alire-project/alire-index"
MANIFEST_PATH = re.compile(r"^index/[^/]+/([^/]+)/\1-[^/]+\.toml$")


def get_json(path: str, **params: str | int) -> object:
    url = f"{API}{path}"
    if params:
        url += "?" + urlencode(params)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "flyology-community-catalog",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def pages(path: str, **params: str | int) -> list[dict]:
    items = []
    page = 1
    while True:
        batch = get_json(path, per_page=100, page=page, **params)
        if not isinstance(batch, list):
            raise ValueError(f"GitHub returned an unexpected response for {path}")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def snapshot() -> dict:
    repository = get_json("")
    if not isinstance(repository, dict):
        raise ValueError("GitHub returned an unexpected repository response")
    base = repository["default_branch"]
    pulls = pages("/pulls", state="open", base=base)
    records = []
    for pull in pulls:
        number = pull["number"]
        try:
            files = pages(f"/pulls/{number}/files")
        except HTTPError as error:
            if error.code == 404:
                # The PR may have closed while this snapshot was being collected.
                continue
            raise
        manifests = [
            {
                "path": file["filename"],
                "status": file["status"],
                "additions": file["additions"],
                "deletions": file["deletions"],
            }
            for file in files
            if MANIFEST_PATH.fullmatch(file["filename"])
        ]
        if not manifests:
            continue
        try:
            details = get_json(f"/pulls/{number}")
        except HTTPError as error:
            if error.code == 404:
                # The PR may have closed while this snapshot was being collected.
                continue
            raise
        if not isinstance(details, dict):
            raise ValueError(f"GitHub returned an unexpected response for PR #{number}")
        records.append(
            {
                "number": number,
                "title": details["title"],
                "created_at": details["created_at"],
                "updated_at": details["updated_at"],
                "draft": details["draft"],
                "comments": details["comments"],
                "review_comments": details["review_comments"],
                "commits": details["commits"],
                "manifests": manifests,
            }
        )
    return {
        "base": base,
        "fetched_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "pulls": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
