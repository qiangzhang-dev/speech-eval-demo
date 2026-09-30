"""Metric correctness, import failures, offline use and safe report rendering."""
import csv
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from speech_eval.comparison import compare_pairs, load_pairs, render_comparison, write_comparison


def row(identifier, reference, baseline, candidate):
    return dict(zip(('sample_id', 'reference', 'baseline', 'candidate'), (identifier, reference, baseline, candidate)))


class ComparisonTests(unittest.TestCase):
    def test_metadata_is_explicit_escaped_and_separate_from_result_data(self):
        report = compare_pairs([row('one', 'hello', 'hello', 'hello')])
        report['provenance'] = {'synthetic': False, 'baseline_name': 'old',
                                'candidate_name': 'new', 'input_sha256': 'abc'}
        original = json.dumps(report, sort_keys=True)
        generic = render_comparison(report)
        self.assertIn('<title>转写版本对比 · Nate Zhang</title>', generic)
        self.assertIn('1 条导入转写文本', generic)
        self.assertNotIn('rel="canonical"', generic)
        self.assertNotIn('单版本结果', generic)
        self.assertIn('合成样例演示', generic)
        custom = render_comparison(report, title='<test> & title',
                                   description='"description" <tag>',
                                   canonical_url='https://example.org/report/?a=1&b=2')
        self.assertIn('<title>&lt;test&gt; &amp; title</title>', custom)
        self.assertIn('content="&quot;description&quot; &lt;tag&gt;"', custom)
        self.assertIn('href="https://example.org/report/?a=1&amp;b=2"', custom)
        self.assertEqual(json.dumps(report, sort_keys=True), original)

    def test_default_synthetic_cli_metadata_and_custom_input_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, arguments in [('synthetic', []),
                                    ('imported', ['--input', str(ROOT/'data/comparison/pairs.csv')])]:
                output = Path(tmp)/name
                command = [sys.executable, str(ROOT/'scripts/compare.py'),
                           '--output', str(output), *arguments]
                result = subprocess.run(command, cwd=tmp, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                page = (output/'index.html').read_text(encoding='utf-8')
                if name == 'synthetic':
                    self.assertIn('<title>6 条合成样例转写对比 · Nate Zhang</title>', page)
                    self.assertIn('未运行真实语音模型', page)
                    self.assertIn('<link rel="canonical" href="https://qiangzhang-dev.github.io/speech-eval/compare/">', page)
                else:
                    self.assertNotIn('rel="canonical"', page)
                    self.assertIn('6 条导入转写文本', page)

    def test_weighting_and_opposing_changes(self):
        report = compare_pairs([row('short', '甲乙', '甲', '甲乙'), row('long', 'abcdefgh', 'abcdefgh', 'abcdef')])
        stats = report['summary']
        self.assertEqual(stats['counts'], dict(improved=1, regressed=1, unchanged=0, excluded=0))
        self.assertEqual(stats['baseline']['macro_cer'], .25)
        self.assertEqual(stats['candidate']['macro_cer'], .125)
        self.assertEqual(stats['baseline']['corpus_cer'], .1)
        self.assertEqual(stats['candidate']['corpus_cer'], .2)
        self.assertLess(stats['delta']['macro_cer'], 0)
        self.assertGreater(stats['delta']['corpus_cer'], 0)
        self.assertEqual(report['samples'][0]['baseline_metric']['operations'][0]['reference'], '乙')

    def test_empty_reference_empty_output_and_above_one(self):
        report = compare_pairs([row('empty', '，！', '', '甲'), row('deletion', '甲乙', '', ''), row('insert', '甲', '甲', '甲乙丙')])
        stats = report['summary']
        self.assertEqual(stats['paired'], 2)
        self.assertEqual(stats['counts']['excluded'], 1)
        self.assertEqual(report['samples'][0]['delta'], None)
        self.assertEqual(report['samples'][1]['baseline_metric']['value'], 1)
        self.assertEqual(report['samples'][2]['candidate_metric']['value'], 2)
        empty = compare_pairs([row('empty', '', '', '')])
        self.assertIsNone(empty['summary']['baseline']['macro_cer'])
        self.assertIsNone(empty['summary']['delta']['corpus_cer'])

    def test_normalization_and_equal_scores_do_not_hide_outputs(self):
        report = compare_pairs([row('same', 'ＡB，甲', 'ab甲', 'AB甲！'), row('different', '甲乙', '甲丙', '丁乙')])
        self.assertEqual(report['summary']['counts']['unchanged'], 2)
        self.assertEqual(report['samples'][0]['baseline_metric']['value'], 0)
        self.assertNotEqual(report['samples'][1]['baseline_metric']['operations'], report['samples'][1]['candidate_metric']['operations'])

    def test_invalid_import_is_rejected_before_creating_output(self):
        header = 'sample_id,reference,baseline,candidate\n'
        inputs = [header, header+'x,甲,甲,甲\nx,乙,乙,乙\n', header+' ,甲,甲,甲\n',
                  header+'x,甲,甲\n', header+'x,甲,甲,甲,extra\n',
                  'sample_id,reference,baseline,baseline,candidate\nx,甲,甲,甲,甲\n',
                  header+'x,'+'甲'*1001+',甲,甲\n']
        with tempfile.TemporaryDirectory() as tmp:
            path, output = Path(tmp)/'input.csv', Path(tmp)/'out'
            for text in inputs:
                with self.subTest(text=text[:80]):
                    path.write_text(text, encoding='utf-8')
                    with self.assertRaises(ValueError): write_comparison(path, output)
                    self.assertFalse(output.exists())

    def test_bom_multiline_escaping_and_repeatability_offline(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')):
            path = Path(tmp)/'input.csv'
            with path.open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=['sample_id','reference','baseline','candidate'])
                writer.writeheader()
                writer.writerow(row('=1+1', '你好,\n世界', '<script>alert(1)</script>', '你好世界'))
            rows, _ = load_pairs(path)
            self.assertEqual(rows[0]['reference'], '你好,\n世界')
            first, second = Path(tmp)/'first', Path(tmp)/'second'
            for dest in (first, second):
                write_comparison(path, dest, baseline_name='<img src=x onerror=alert(1)>')
            content = (first/'index.html').read_text()
            self.assertNotIn('<script>alert(1)</script>', content)
            self.assertNotIn('<img src=x', content)
            self.assertIn('&lt;script&gt;', content)
            self.assertEqual((first/'comparison.json').read_bytes(), (second/'comparison.json').read_bytes())
            with (first/'comparison.csv').open(encoding='utf-8-sig',newline='') as stream:
                result=list(csv.DictReader(stream))[0]
            self.assertEqual(result['sample_id'], "'=1+1")
            self.assertEqual(json.loads((first/'comparison.json').read_text())['samples'][0]['sample_id'], '=1+1')

    def test_cli_outside_repo_custom_import_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'report with spaces'
            command=[sys.executable,str(ROOT/'scripts/compare.py'),'--input',str(ROOT/'data/comparison/pairs.csv'),'--output',str(output)]
            result=subprocess.run(command,cwd=tmp,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            saved=(output/'comparison.json').read_bytes()
            report=json.loads(saved)
            self.assertEqual(report['summary']['counts'],dict(improved=2,regressed=2,unchanged=2,excluded=0))
            self.assertFalse(report['provenance']['synthetic'])
            result=subprocess.run(command,cwd=tmp,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((output/'comparison.json').read_bytes(),saved)

    def test_check_only_and_invalid_input_leave_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            source = work/'my pairs.csv'
            source.write_bytes((ROOT/'data/comparison/starter.csv').read_bytes())
            command = [sys.executable, str(ROOT/'scripts/compare.py'), '--input', str(source)]
            result = subprocess.run(command+['--check'], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('3 samples', result.stdout)
            self.assertEqual(list(work.iterdir()), [source])
            source.write_text('sample_id,reference,baseline,candidate\nx,one,one,one\nx,two,two,two\n')
            result = subprocess.run(command+['--output', str(work/'report')], cwd=tmp, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unique', result.stderr)
            self.assertFalse((work/'report').exists())

    def test_open_failure_keeps_successful_report(self):
        sys.path.insert(0, str(ROOT))
        from scripts.compare import main
        with tempfile.TemporaryDirectory() as tmp, patch('scripts.compare.webbrowser.open', return_value=False) as launch:
            dest = Path(tmp)/'report with spaces'
            self.assertEqual(main(['--input', str(ROOT/'data/comparison/starter.csv'), '--output', str(dest), '--open']), 0)
            launch.assert_called_once_with((dest/'index.html').as_uri())
            self.assertTrue((dest/'comparison.json').exists())


if __name__ == '__main__':
    unittest.main()
