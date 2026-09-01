from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class CoreMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from speech_eval.metrics import compute_cer
        except (ImportError, ModuleNotFoundError) as exc:
            raise unittest.SkipTest(f"core metric module is not ready: {exc}")
        cls.compute_cer = staticmethod(compute_cer)

    def test_exact_match_is_zero_and_deterministic(self):
        first = self.compute_cer("你好，世界", "你好世界")
        second = self.compute_cer("你好，世界", "你好世界")
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.value, 0.0)
        self.assertEqual(first.substitutions, 0)
        self.assertEqual(first.deletions, 0)
        self.assertEqual(first.insertions, 0)
        self.assertEqual(first.operations, [])

    def test_gold_standard_substitution_deletion_insertion(self):
        substitution = self.compute_cer("abc", "axc")
        self.assertEqual((substitution.substitutions, substitution.deletions, substitution.insertions), (1, 0, 0))
        self.assertAlmostEqual(substitution.value, 1 / 3)
        deletion = self.compute_cer("abc", "ac")
        self.assertEqual((deletion.substitutions, deletion.deletions, deletion.insertions), (0, 1, 0))
        self.assertEqual(deletion.operations[0]["hypothesis"], "-")
        self.assertEqual(deletion.operations[0]["hypothesis_index"], "-")
        insertion = self.compute_cer("abc", "abxc")
        self.assertEqual((insertion.substitutions, insertion.deletions, insertion.insertions), (0, 0, 1))
        insertion_op = next(item for item in insertion.operations if item["op"] == "insertion")
        self.assertEqual(insertion_op["reference"], "-")

    def test_empty_reference_is_explicitly_not_applicable(self):
        result = self.compute_cer("-", "abc")
        self.assertIsNone(result.value)
        self.assertFalse(result.applicable)
        self.assertTrue(result.reason)

    def test_boundary_values_are_reproducible(self):
        self.assertEqual(self.compute_cer("abcdefghij", "abcdefghi-").value, 0.1)
        self.assertEqual(self.compute_cer("abcdefghij", "abcdefgh--").value, 0.2)


if __name__ == "__main__":
    unittest.main()
