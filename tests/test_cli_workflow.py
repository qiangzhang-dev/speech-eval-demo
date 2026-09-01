from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "speech_eval", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class CliWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / "src" / "speech_eval").exists():
            raise unittest.SkipTest("core speech_eval package is not initialized")
        probe = _run("--help", cwd=ROOT)
        if probe.returncode != 0:
            raise unittest.SkipTest(
                "core CLI is not initialized yet: " + (probe.stderr or probe.stdout).splitlines()[-1]
            )

    def test_validate_run_export_round_trip(self):
        generator = ROOT / "scripts" / "generate_samples.py"
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            data = work / "data"
            db = work / "evaluation.db"
            exports = work / "exports"
            generated = subprocess.run(
                [sys.executable, str(generator), "--count", "6", "--output", str(data)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn('"count": 6', generated.stdout)
            validate = _run("validate", str(data / "manifest.csv"), cwd=ROOT)
            self.assertEqual(validate.returncode, 0, validate.stderr + validate.stdout)
            run = _run("run", str(data / "manifest.csv"), "--database", str(db), cwd=ROOT)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            self.assertTrue(db.exists() and db.stat().st_size > 0)
            export = _run("export", "--database", str(db), "--output", str(exports), cwd=ROOT)
            self.assertEqual(export.returncode, 0, export.stderr + export.stdout)
            self.assertTrue(any(exports.iterdir()))

    def test_bad_manifest_isolated_with_nonzero_diagnostic(self):
        generator = ROOT / "scripts" / "generate_samples.py"
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            data = work / "data"
            subprocess.run(
                [sys.executable, str(generator), "--count", "2", "--output", str(data)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            manifest = data / "manifest.csv"
            content = manifest.read_text(encoding="utf-8-sig")
            manifest.write_text(content.replace("SYN-0001", "", 1), encoding="utf-8-sig")
            result = _run("validate", str(manifest), cwd=ROOT)
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(result.stdout + result.stderr, r"样例|sample|ID|invalid|error")


if __name__ == "__main__":
    unittest.main()
