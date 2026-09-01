from __future__ import annotations

import unittest

from speech_eval.__main__ import _parser


class ProviderCliConfigurationTests(unittest.TestCase):
    def test_openai_compatible_provider_is_selectable_but_explicit(self):
        args = _parser().parse_args(
            [
                "run",
                "manifest.csv",
                "--provider",
                "openai-compatible",
                "--model",
                "local-model",
                "--api-base",
                "http://127.0.0.1:9000/v1",
                "--allow-network",
            ]
        )
        self.assertEqual(args.provider, "openai-compatible")
        self.assertEqual(args.model, "local-model")
        self.assertTrue(args.allow_network)

    def test_rules_remain_the_safe_default(self):
        args = _parser().parse_args(["run", "manifest.csv"])
        self.assertEqual(args.provider, "rules")
        self.assertFalse(args.allow_network)


if __name__ == "__main__":
    unittest.main()
