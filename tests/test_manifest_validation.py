from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from scripts.generate_samples import generate  # noqa: E402


class ManifestValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from speech_eval.manifest import validate_manifest
        except (ImportError, ModuleNotFoundError) as exc:
            raise unittest.SkipTest(f"core manifest module is not ready: {exc}")
        cls.validate_manifest = staticmethod(validate_manifest)

    @staticmethod
    def _write_path_manifest(directory: Path, path_value: str) -> Path:
        manifest = directory / "manifest.json"
        manifest.write_text(
            json.dumps(
                [
                    {
                        "sample_id": "PATH-0001",
                        "scene_type": "短语音/录音转写",
                        "task_types": ["语音识别"],
                        "input_data": {"path": path_value},
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return manifest

    def test_generated_manifest_loads_and_resolves_input_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            generate(6, output)
            report = self.validate_manifest(output / "manifest.csv", check_paths=False)
            self.assertTrue(report.ok, [issue.to_dict() for issue in report.issues])
            self.assertEqual(len(report.samples), 6)
            self.assertEqual(len({sample.sample_id for sample in report.samples}), 6)

    def test_duplicate_and_missing_id_are_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            generate(3, output)
            manifest = output / "manifest.csv"
            rows = []
            with manifest.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = reader.fieldnames
                rows = list(reader)
            rows[1]["sample_id"] = rows[0]["sample_id"]
            rows[2]["sample_id"] = ""
            with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            report = self.validate_manifest(manifest, check_paths=False)
            codes = {issue.code for issue in report.issues}
            self.assertIn("DUPLICATE_SAMPLE_ID", codes)
            self.assertIn("MISSING_SAMPLE_ID", codes)
            self.assertFalse(report.ok)

    def test_label_source_conflict_is_visible_but_non_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            generate(3, output)
            manifest = output / "manifest.csv"
            with manifest.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = reader.fieldnames
                rows = list(reader)
            rows[1]["subscene_type"] = "普通话口述"
            with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            report = self.validate_manifest(manifest, check_paths=True)
            conflicts = [issue for issue in report.issues if issue.code == "LABEL_METADATA_CONFLICT"]
            self.assertTrue(conflicts)
            self.assertTrue(report.ok)
            self.assertTrue(all(issue.severity == "warning" for issue in conflicts))

    def test_relative_input_path_inside_manifest_root_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bundle"
            inputs = root / "inputs"
            inputs.mkdir(parents=True)
            inputs.joinpath("sample.wav").write_bytes(b"audio")
            manifest = self._write_path_manifest(root, "inputs/sample.wav")

            report = self.validate_manifest(manifest, check_paths=True)

            self.assertTrue(report.ok, [issue.to_dict() for issue in report.issues])

    def test_parent_and_absolute_paths_outside_manifest_root_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            root = workspace / "bundle"
            root.mkdir()
            outside = workspace / "outside.wav"
            outside.write_bytes(b"audio")

            for path_value in ("../outside.wav", str(outside.resolve())):
                with self.subTest(path_value=path_value):
                    manifest = self._write_path_manifest(root, path_value)
                    report = self.validate_manifest(manifest, check_paths=True)
                    codes = {issue.code for issue in report.issues}
                    self.assertIn("INPUT_PATH_OUTSIDE_MANIFEST_ROOT", codes)
                    self.assertFalse(report.ok)

    def test_symlink_cannot_escape_manifest_root(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            root = workspace / "bundle"
            outside = workspace / "outside"
            root.mkdir()
            outside.mkdir()
            outside.joinpath("sample.wav").write_bytes(b"audio")
            link = root / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")
            manifest = self._write_path_manifest(root, "linked/sample.wav")

            report = self.validate_manifest(manifest, check_paths=True)

            codes = {issue.code for issue in report.issues}
            self.assertIn("INPUT_PATH_OUTSIDE_MANIFEST_ROOT", codes)
            self.assertFalse(report.ok)


if __name__ == "__main__":
    unittest.main()
