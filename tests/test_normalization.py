from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from speech_eval._normalization import (  # noqa: E402
    NORMALIZATION_VERSION as AUTHORITATIVE_VERSION,
)
from speech_eval._normalization import normalize_text as authoritative_normalize  # noqa: E402
from speech_eval.normalization import NORMALIZATION_VERSION, normalize_text  # noqa: E402


class NormalizationTests(unittest.TestCase):
    def test_public_package_reexports_authoritative_implementation(self):
        self.assertIs(normalize_text, authoritative_normalize)
        self.assertEqual(NORMALIZATION_VERSION, AUTHORITATIVE_VERSION)

    def test_shadow_module_reexports_authoritative_implementation(self):
        path = ROOT / "src" / "speech_eval" / "normalization.py"
        spec = importlib.util.spec_from_file_location("speech_eval.normalization_shadow", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertIs(module.normalize_text, authoritative_normalize)
        self.assertEqual(module.NORMALIZATION_VERSION, AUTHORITATIVE_VERSION)

    def test_nfkc_fullwidth_latin_digits_and_casefold(self):
        self.assertEqual(normalize_text("ＡＢＣ１２３ Straße ÉCOLE"), "abc123strasseécole")

    def test_han_extension_and_compatibility_ranges_are_preserved(self):
        self.assertEqual(normalize_text("\u3400\u4dbf\u4e00\u9fff"), "\u3400\u4dbf\u4e00\u9fff")
        self.assertTrue(normalize_text("\ufa11"))

    def test_punctuation_whitespace_emoji_and_symbols_are_removed(self):
        self.assertEqual(normalize_text(" A，\t中。🙂 +_B "), "a中b")

    def test_non_latin_letters_are_not_preserved(self):
        self.assertEqual(normalize_text("AΩЖشB"), "ab")

    def test_unicode_decimal_digits_are_preserved(self):
        self.assertEqual(normalize_text("1１١"), "11١")

    def test_empty_none_and_unknown_version(self):
        self.assertEqual(normalize_text(None), "")
        self.assertEqual(normalize_text(""), "")
        with self.assertRaisesRegex(ValueError, "unsupported normalization version"):
            normalize_text(None, version="cer-normalize-v2")


if __name__ == "__main__":
    unittest.main()
