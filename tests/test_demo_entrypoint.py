"""Exercise the public quickstart from outside the repository."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.demo import run_demo


class DemoEntrypointTests(unittest.TestCase):
    def test_offline_and_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')):
            runs = [Path(tmp) / name for name in ('first', 'second')]
            for output in runs:
                output.mkdir()
                summary = run_demo(output)
                self.assertEqual(summary['counts']['completed'], 6)
                self.assertEqual(summary['counts']['failed'], 0)
                self.assertEqual(summary['metric_summary']['count'], 6)
                self.assertFalse(summary['reproduction']['network_used'])
                self.assertIn('合成文本样例', (output / 'index.html').read_text(encoding='utf-8'))
            self.assertEqual((runs[0] / 'evaluation-results.jsonl').read_bytes(),
                             (runs[1] / 'evaluation-results.jsonl').read_bytes())
            rows = [json.loads(line) for line in (runs[0] / 'evaluation-results.jsonl').read_text(encoding='utf-8').splitlines()]
            missing_unit = next(row for row in rows if row['样例ID'] == 'SYN-0002')
            self.assertEqual(missing_unit['量化指标']['value'], 1 / 16)
            self.assertTrue(any(e['type'] == 'text_diff' and e['content']['reference'] == '份' for e in missing_unit['证据片段']))

    def test_cli_any_directory_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'demo with spaces'
            command = [sys.executable, str(ROOT / 'scripts/demo.py'), '--output', str(output)]
            env = os.environ.copy()
            env['SPEECH_EVAL_ANALYZER'] = 'openai-compatible'
            env['SPEECH_EVAL_ALLOW_REMOTE'] = 'true'
            first = subprocess.run(command, cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            saved = (output / 'evaluation-results.jsonl').read_bytes()
            second = subprocess.run(command, cwd=tmp, env=env, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual((output / 'evaluation-results.jsonl').read_bytes(), saved)


if __name__ == '__main__':
    unittest.main()
