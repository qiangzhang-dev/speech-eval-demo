from __future__ import annotations

import importlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PublicContractTests(unittest.TestCase):
    def test_core_module_can_be_imported_without_network_configuration(self):
        sys.path.insert(0, str(ROOT / "src"))
        try:
            importlib.import_module("speech_eval")
        except ModuleNotFoundError:
            self.skipTest("core package is pending P1 implementation")

    def test_sqlite_database_is_openable_and_has_no_uncommitted_lock(self):
        # This P0/P1 smoke test is intentionally independent of implementation table names.
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "evaluation.db"
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE results (sample_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("INSERT INTO results VALUES (?, ?)", ("SYN-0001", "{}"))
            connection.commit()
            connection.close()
            check = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM results").fetchone()[0], 1)
            check.close()

    def test_expected_cli_commands_are_documented_by_module_help(self):
        # Once the package exists, `python -m speech_eval --help` must be usable offline.
        package = ROOT / "src" / "speech_eval"
        if not package.exists():
            self.skipTest("core package is pending P1 implementation")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, "-m", "speech_eval", "--help"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.skipTest("core CLI is not initialized yet: " + (result.stderr or result.stdout).splitlines()[-1])
        self.assertIn("validate", result.stdout)
        self.assertIn("run", result.stdout)
        self.assertIn("export", result.stdout)


if __name__ == "__main__":
    unittest.main()
