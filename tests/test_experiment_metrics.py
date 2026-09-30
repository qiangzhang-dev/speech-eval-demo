"""Keep the experimental English WER recipe explicit and independently checked."""
import sys
import json
import hashlib
import shutil
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.librispeech_experiment import render_experiment_report, word_error, word_tokens, summarize_words
from speech_eval.comparison import compare_pairs, load_pairs, render_comparison


class ExperimentMetricTests(unittest.TestCase):
    def test_regenerated_report_preserves_data_and_identifies_real_recordings(self):
        source = ROOT/'experiments/librispeech-beam/results'
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'results'
            shutil.copytree(source, output)
            saved = (output/'report/comparison.json').read_bytes()
            render_experiment_report(output)
            self.assertEqual((output/'report/comparison.json').read_bytes(), saved)
            page = (output/'report/index.html').read_text(encoding='utf-8')
            self.assertIn('<title>Whisper beam 1 vs 5：40 条录音逐句对比 · Nate Zhang</title>', page)
            self.assertIn('40 条真实录音', page)
            self.assertIn('<link rel="canonical" href="https://qiangzhang-dev.github.io/notes/whisper-beam/report/">', page)
            self.assertIn('合成样例演示', page)
            self.assertNotIn('单版本结果', page)
            self.assertEqual(page.count('<article class="case"'), 40)
            render_experiment_report(output)
            self.assertEqual((output/'report/index.html').read_text(encoding='utf-8'), page)

    def test_known_word_edits_and_normalization(self):
        self.assertEqual(word_error('ONE TWO THREE FOUR', 'one too four')['errors'], 2)
        self.assertEqual(word_error('one', 'one two three')['wer'], 2)
        self.assertIsNone(word_error('', 'one')['wer'])
        self.assertEqual(word_tokens("Ｉ can't, 12."), ['i', "can't", '12'])
        self.assertNotEqual(word_tokens('twelve'), word_tokens('12'))

    def test_word_boundaries_and_reference_spelling(self):
        # Same normalized characters, different tokens; WER alone is not semantic correctness.
        for reference, hypothesis, expected in [('YOU ARE ACUTE', 'You are a cute.', 2/3),
                                                 ('TO DAY I SHOUTED', 'Today I shouted.', .5)]:
            row = compare_pairs([{'sample_id': 'case', 'reference': reference,
                                  'baseline': hypothesis, 'candidate': hypothesis}])['samples'][0]
            self.assertEqual(row['baseline_metric']['value'], 0)
            self.assertEqual(word_error(reference, hypothesis)['wer'], expected)

    def test_corpus_weighting(self):
        records = [{'reference': 'one', 'baseline': {'text': ''}, 'candidate': {'text': 'one'}},
                   {'reference': 'one two three', 'baseline': {'text': 'one two three'}, 'candidate': {'text': 'one'}}]
        result = summarize_words(records)
        self.assertEqual(result['baseline']['corpus_wer'], .25)
        self.assertEqual(result['candidate']['corpus_wer'], .5)
        self.assertEqual(result['candidate']['reference_words'], 4)

    def test_published_results_are_derived_from_all_frozen_samples(self):
        directory = ROOT / 'experiments/librispeech-beam/results'
        manifest = json.loads((directory/'manifest.json').read_text())
        records = [json.loads(line) for line in (directory/'raw-results.jsonl').read_text().splitlines()]
        pairs, _ = load_pairs(directory/'pairs.csv')
        summary = json.loads((directory/'summary.json').read_text())
        self.assertEqual(len(manifest), 40)
        self.assertEqual(len(set(row['speaker_id'] for row in manifest)), 40)
        self.assertEqual([r['sample_id'] for r in manifest], [r['sample_id'] for r in records])
        self.assertEqual([r['sample_id'] for r in records], [r['sample_id'] for r in pairs])
        for sample, raw, pair in zip(manifest, records, pairs):
            self.assertEqual(raw['reference'], sample['reference'])
            self.assertEqual(pair['reference'], sample['reference'])
            self.assertEqual(pair['baseline'], raw['baseline']['text'])
            self.assertEqual(pair['candidate'], raw['candidate']['text'])
        self.assertEqual(compare_pairs(pairs)['summary'], summary['cer'])
        self.assertEqual(summarize_words(records), summary['wer'])
        report = json.loads((directory/'report/comparison.json').read_text())
        flagged = []
        for row in report['samples']:
            for side in ('baseline', 'candidate'):
                self.assertEqual({key: row[side+'_word_metric'][key] for key in ('errors', 'reference_words', 'wer')},
                                 word_error(row['reference'], row[side]))
            if any(row[side+'_metric']['value'] == 0 and row[side+'_word_metric']['errors'] > 0 for side in ('baseline', 'candidate')):
                flagged.append(row['sample_id'])
        self.assertEqual(flagged, ['121-127105-0014', '8455-210777-0063'])
        page = render_comparison(report)
        self.assertEqual(page.count('data-word-error="true"'), 2)
        self.assertIn('WER 66.67%', page)
        self.assertEqual(report['word_summary'], summary['wer'])
        provenance = json.loads((directory/'environment.json').read_text())
        self.assertEqual(hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest(), provenance['manifest_sha256'])
        self.assertEqual((directory/'protocol.json').read_bytes(), (directory.parent/'protocol.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
