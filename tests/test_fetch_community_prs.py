from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch-community-prs.py"
SPEC = importlib.util.spec_from_file_location("fetch_community_prs", SCRIPT)
assert SPEC and SPEC.loader
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)


class FetchCommunityPrsTests(unittest.TestCase):
    def test_snapshot_uses_default_branch_and_manifest_files(self) -> None:
        pulls = [{"number": 42}]
        files = [
            {"filename": "index/au/aunit/aunit-1.1.0.toml", "status": "added", "additions": 20, "deletions": 0},
            {"filename": "index/index.toml", "status": "modified", "additions": 1, "deletions": 1},
            {"filename": "README.md", "status": "modified", "additions": 1, "deletions": 1},
        ]
        details = {
            "title": "aunit 1.1.0",
            "created_at": "2026-09-13T12:00:00Z",
            "updated_at": "2026-09-16T11:00:00Z",
            "draft": False,
            "comments": 2,
            "review_comments": 1,
            "commits": 3,
        }
        with patch.object(fetch, "get_json", side_effect=[{"default_branch": "stable-1.4.0"}, details]), patch.object(
            fetch, "pages", side_effect=[pulls, files]
        ) as page_calls:
            result = fetch.snapshot()
        self.assertEqual(page_calls.call_args_list[0].args, ("/pulls",))
        self.assertEqual(page_calls.call_args_list[0].kwargs["base"], "stable-1.4.0")
        self.assertEqual(result["base"], "stable-1.4.0")
        self.assertEqual(len(result["pulls"]), 1)
        self.assertEqual(result["pulls"][0]["manifests"], [
            {"path": "index/au/aunit/aunit-1.1.0.toml", "status": "added", "additions": 20, "deletions": 0}
        ])


if __name__ == "__main__":
    unittest.main()
