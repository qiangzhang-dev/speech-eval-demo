"""Keep the experimental English WER recipe explicit and independently checked."""
import sys
import json
import hashlib
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.librispeech_experiment import word_error, word_tokens, summarize_words
from speech_eval.comparison import compare_pairs, load_pairs


class ExperimentMetricTests(unittest.TestCase):
    def test_known_word_edits_and_normalization(self):
        self.assertEqual(word_error('ONE TWO THREE FOUR', 'one too four')['errors'], 2)
        self.assertEqual(word_error('one', 'one two three')['wer'], 2)
        self.assertIsNone(word_error('', 'one')['wer'])
        self.assertEqual(word_tokens("Ｉ can't, 12."), ['i', "can't", '12'])
        self.assertNotEqual(word_tokens('twelve'), word_tokens('12'))

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
        provenance = json.loads((directory/'environment.json').read_text())
        self.assertEqual(hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest(), provenance['manifest_sha256'])
        self.assertEqual((directory/'protocol.json').read_bytes(), (directory.parent/'protocol.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
