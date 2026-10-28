"""Exercise map precedence, fast lookups, and searches for incomplete maps."""

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from el_xref.build import render_tsv
from el_xref.config import Client, Config, Repo, Spec
from el_xref.index import Symbol, digest, index_path
from el_xref.lookup import find
from el_xref.mapped import load_map, location
from el_xref.render import show, where
from el_xref.touch import touch


class LookupTests(unittest.TestCase):
    """Use tiny local repos to test the CLI's data and checkout boundaries."""

    def setUp(self) -> None:
        """Create a complete mapping and a second client needing a search."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "xref"
        self.spec_repo = Repo(
            "spec", "https://example.org/spec", "main", (), "python"
        )
        self.repos = {
            name: Repo(
                name, f"https://example.org/{name}", "main", ("core",), "go"
            )
            for name in ("mapped", "pending")
        }
        self.config = Config(
            root,
            Spec(self.spec_repo, "amsterdam"),
            {n: Client(n, (r,)) for n, r in self.repos.items()},
        )
        self.config.data.mkdir(parents=True)
        self.sid = "vm.gas.memory_cost"
        self.symbol = Symbol(
            self.sid,
            "spec",
            "vm/gas.py",
            "memory_cost",
            "memory_cost",
            1,
            3,
            "python",
            "spec-hash",
        )
        index = index_path(self.config, self.spec_repo)
        index.parent.mkdir(parents=True)
        index.write_text(json.dumps(asdict(self.symbol)) + "\n")
        self.body = "func MemoryCost(size uint64) uint64 { return size * 3 }"
        for name in self.repos:
            path = self.config.repo_cache / name
            (path / "core").mkdir(parents=True)
            (path / "core/gas.go").write_text(
                "package core\n" + self.body + "\n"
            )
            self.git(path, "init", "-q")
            self.git(path, "add", ".")
            self.git(
                path,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.org",
                "commit",
                "-qm",
                "fixture",
            )
        self.row = {
            "repo": "mapped",
            "path": "core/gas.go",
            "qualname": "MemoryCost",
            "line": 2,
            "end_line": 2,
            "hash": digest(self.body),
            "confidence": "high",
            "role": "entry",
            "steps": [0],
            "votes": 2,
        }
        self.table = {"matches": [self.row], "missing": [], "pipeline": 3}
        self.data = {
            "entries": {
                self.sid: {
                    "steps": [{"id": 0, "text": "Compute memory gas"}],
                    "clients": {
                        "mapped": self.table,
                        "pending": {"error": "limit"},
                    },
                }
            },
            "repos": {n: "abc123" for n in self.repos},
        }
        self.save()

    def git(self, path: Path, *args: str) -> str:
        """Run git against a disposable fixture."""
        return subprocess.check_output(
            ["git", "-C", str(path), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        )

    def save(self) -> None:
        """Persist fixture changes."""
        (self.config.data / "tree.json").write_text(json.dumps(self.data))

    def capture(self, function, *args, **kwargs) -> str:
        """Capture user-facing command output."""
        output = io.StringIO()
        with redirect_stdout(output):
            function(*args, **kwargs)
        return output.getvalue()

    def test_complete_map_needs_no_index_or_client_search(self) -> None:
        """A complete map query stays fast even without local indexes."""
        index_path(self.config, self.spec_repo).unlink()
        with patch("el_xref.lookup.client_index", side_effect=AssertionError):
            out = self.capture(
                find, self.config, "memory_cost", ["mapped"], {}, 4
            )
        self.assertIn("MemoryCost", out)
        self.assertIn("mapped at mapped@abc123", out)
        self.assertIn(str(self.config.repo_cache / "mapped/core/gas.go"), out)

    def test_failed_and_absent_pairs_search_only_the_needed_client(
        self,
    ) -> None:
        """Failed, absent, and empty pairs all fall back to current source."""
        from el_xref.lookup import client_index

        for table in ({"error": "limit"}, {}, {"matches": []}, None):
            with self.subTest(table=table):
                tables = self.data["entries"][self.sid]["clients"]
                tables["pending"] = table
                if table is None:
                    del tables["pending"]
                self.save()
                with patch(
                    "el_xref.lookup.client_index", wraps=client_index
                ) as search:
                    out = self.capture(
                        find, self.config, "memory_cost", [], {}, 2
                    )
                self.assertEqual(
                    [c.args[1].name for c in search.call_args_list],
                    ["pending"],
                )
                self.assertIn("Keyword candidates", out)
                self.assertIn("MemoryCost", out)

    def test_partial_steps_also_search_for_gaps(self) -> None:
        """Existing rows do not hide a known gap in step coverage."""
        self.table["missing"] = [1]
        self.save()
        out = self.capture(find, self.config, "memory_cost", ["mapped"], {}, 2)
        self.assertIn("unmapped steps: 1", out)
        self.assertIn("Keyword candidates", out)

    def test_search_flag_bypasses_complete_map(self) -> None:
        """Explicit source searches still work for a complete saved table."""
        out = self.capture(
            find, self.config, "memory_cost", ["mapped"], {}, 2, search=True
        )
        self.assertIn("Keyword candidates", out)
        self.assertIn("    mapped  MemoryCost", out)

    def test_explicit_ref_keeps_its_source_locations(self) -> None:
        """Ref searches retain the requested ref in their source locations."""
        out = self.capture(
            find,
            self.config,
            "memory_cost",
            ["mapped"],
            {"mapped": "@HEAD"},
            2,
        )
        self.assertIn("HEAD:core/gas.go:2", out)

    def test_show_low_confidence_and_reverse_delegation(self) -> None:
        """Low-confidence filtering and delegated reverse links survive."""
        self.row["confidence"] = "low"
        self.table["delegated"] = [dict(self.row, spec="vm.gas.helper")]
        self.save()
        default = self.capture(
            show, self.config, "memory_cost", ["mapped"], False
        )
        low = self.capture(show, self.config, "memory_cost", ["mapped"], True)
        reverse = self.capture(where, self.config, "MemoryCost")
        self.assertNotIn("[entry, low", default)
        self.assertIn("[entry, low", low)
        self.assertIn("->  vm.gas.helper", reverse)

    def test_explicit_search_honors_checkout_and_rejects_changed_row(
        self,
    ) -> None:
        """Search an alternate checkout without trusting stored hashes."""
        alternate = self.config.repo_cache / "pending"
        (alternate / "core/gas.go").write_text(
            "package core\n"
            "func MemoryCost(size uint64) uint64 { return size * 9 }\n"
        )
        out = self.capture(
            find,
            self.config,
            "memory_cost",
            ["mapped"],
            {"mapped": str(alternate)},
            2,
        )
        self.assertIn(str(alternate / "core/gas.go"), out)
        self.assertNotIn("    mapped  MemoryCost", out)
        self.assertIn("Keyword candidates", out)

    def test_show_and_where_use_tree_and_tolerate_failed_pairs(self) -> None:
        """Legacy entries must not replace new results or failed pairs."""
        (self.config.data / "map.json").write_text(json.dumps({"entries": {}}))
        shown = self.capture(show, self.config, "memory_cost", [], False)
        reverse = self.capture(where, self.config, "MemoryCost")
        self.assertIn("MemoryCost", shown)
        self.assertIn("mapping failed", shown)
        self.assertIn(self.sid, reverse)

    def test_unknown_spec_uses_index_then_search(self) -> None:
        """Functions not yet in the map can still be found in the source."""
        self.data["entries"] = {}
        self.save()
        out = self.capture(
            find, self.config, "memory_cost", ["pending"], {}, 2
        )
        self.assertIn("not mapped", out)
        self.assertIn("MemoryCost", out)

    def test_legacy_map_used_only_without_tree(self) -> None:
        """Old checkouts remain queryable without merging obsolete entries."""
        legacy = {"entries": {"legacy": {}}}
        (self.config.data / "map.json").write_text(json.dumps(legacy))
        self.assertIn(self.sid, load_map(self.config)["entries"])
        (self.config.data / "tree.json").unlink()
        self.assertEqual(load_map(self.config), legacy)

    def test_locations_choose_existing_file_then_pinned_link(self) -> None:
        """Missing local clones should not produce dead file paths."""
        local = self.config.root.parent / "local"
        self.config.checkouts["mapped"] = str(local)
        self.assertIn(
            "cache/repos/mapped", location(self.config, self.data, self.row)
        )
        (local / "core").mkdir(parents=True)
        (local / "core/gas.go").write_text(self.body)
        self.assertIn(str(local), location(self.config, self.data, self.row))
        (local / "core/gas.go").unlink()
        (self.config.repo_cache / "mapped/core/gas.go").unlink()
        self.assertEqual(
            location(self.config, self.data, self.row),
            "https://example.org/mapped/blob/abc123/core/gas.go#L2-L2",
        )

    def test_tsv_exports_failed_status_and_current_source_paths(self) -> None:
        """Grepping exports must expose build failures and working paths."""
        target = self.config.root / "map"
        self.capture(render_tsv, self.config, target)
        self.assertIn("\tfailed\t", (target / "pending.tsv").read_text())
        self.assertIn(
            "xref/cache/repos/mapped/core/gas.go:2-2",
            (target / "mapped.tsv").read_text(),
        )

    def test_touch_uses_tree_for_changed_function(self) -> None:
        """Change scoping shares the same mapping as direct lookups."""
        repo = self.config.root.parent / "spec"
        source = repo / "src/ethereum/forks/amsterdam/vm/gas.py"
        source.parent.mkdir(parents=True)
        source.write_text("def memory_cost():\n    return 3\n")
        diff = "+++ b/src/ethereum/forks/amsterdam/vm/gas.py\n@@ -2 +2 @@\n"
        with patch("el_xref.touch.git", return_value=diff):
            out = self.capture(touch, self.config, repo, "HEAD", "")
        self.assertIn("MemoryCost", out)
        self.assertIn("mapping failed", out)


if __name__ == "__main__":
    unittest.main()
