#!/usr/bin/env python3
"""Reproduce the fixed 40-speaker LibriSpeech tiny.en beam comparison."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import socket
import sys
import tarfile
import time
import unicodedata
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from speech_eval.comparison import render_comparison, write_comparison


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def select_samples(archive_path, cache, expected_hash):
    if digest(archive_path) != expected_hash:
        raise ValueError('Dataset archive SHA-256 does not match the frozen protocol')
    with tarfile.open(archive_path, 'r:gz') as archive:
        candidates = {}
        for member in archive.getmembers():
            if member.isfile() and member.name.endswith('.trans.txt'):
                for line in archive.extractfile(member).read().decode('utf-8').splitlines():
                    identifier, reference = line.split(' ', 1)
                    speaker = identifier.split('-')[0]
                    score = hashlib.sha256(('nate-asr-v1:' + identifier).encode()).hexdigest()
                    item = {'sample_id': identifier, 'speaker_id': speaker, 'reference': reference,
                            'selection_hash': score, 'archive_member': str(Path(member.name).parent / (identifier + '.flac'))}
                    if speaker not in candidates or score < candidates[speaker]['selection_hash']:
                        candidates[speaker] = item
        if len(candidates) != 40:
            raise ValueError(f'Expected 40 test-clean speakers, got {len(candidates)}')
        audio_dir = cache / 'selected-audio'
        audio_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for speaker in sorted(candidates, key=int):
            item = candidates[speaker]
            if not re.fullmatch(r'\d+-\d+-\d+', item['sample_id']):
                raise ValueError('Unexpected utterance ID')
            # Extract bytes of only selected regular files; never unpack tar paths.
            member = archive.getmember(item['archive_member'])
            if not member.isfile():
                raise ValueError('Expected a regular FLAC file')
            audio = archive.extractfile(member).read()
            (audio_dir / (item['sample_id'] + '.flac')).write_bytes(audio)
            rows.append({**item, 'audio_sha256': hashlib.sha256(audio).hexdigest()})
    return rows


def word_tokens(text):
    """Simple declared English tokenizer; no number or contraction expansion."""
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*", unicodedata.normalize('NFKC', text).casefold())


def word_error(reference, hypothesis):
    ref, hyp = word_tokens(reference), word_tokens(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, left in enumerate(ref, 1):
        current = [i]
        for j, right in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j-1] + 1, previous[j-1] + (left != right)))
        previous = current
    return {'errors': previous[-1], 'reference_words': len(ref), 'wer': previous[-1] / len(ref) if ref else None}


def summarize_words(records):
    values = {side: [word_error(r['reference'], r[side]['text']) for r in records] for side in ('baseline', 'candidate')}
    totals = {}
    for side, metrics in values.items():
        errors = sum(x['errors'] for x in metrics)
        words = sum(x['reference_words'] for x in metrics)
        totals[side] = {'errors': errors, 'reference_words': words, 'corpus_wer': errors / words if words else None}
    return {'tokenizer': "NFKC + casefold; regex [a-z0-9]+(?:'[a-z0-9]+)*; no number/contraction expansion; not the official LibriSpeech scoring recipe", **totals}


def render_experiment_report(output):
    """Rebuild display metrics from saved transcripts, without running inference."""
    report = json.loads((output / 'report/comparison.json').read_text(encoding='utf-8'))
    records = [json.loads(line) for line in (output / 'raw-results.jsonl').read_text(encoding='utf-8').splitlines()]
    lookup = {record['sample_id']: record for record in records}
    if len(lookup) != len(records) or set(lookup) != {row['sample_id'] for row in report['samples']}:
        raise ValueError('Raw outputs and comparison sample IDs must match')
    for row in report['samples']:
        raw = lookup[row['sample_id']]
        for side in ('baseline', 'candidate'):
            if row['reference'] != raw['reference'] or row[side] != raw[side]['text']:
                raise ValueError('Saved comparison does not match raw outputs')
            row[side + '_word_metric'] = {**word_error(row['reference'], row[side]),
                'reference_tokens': word_tokens(row['reference']), 'hypothesis_tokens': word_tokens(row[side])}
    report['word_summary'] = summarize_words(records)
    report['provenance']['experiment'] = 'LibriSpeech test-clean · 40 speakers / 40 utterances · Whisper tiny.en · CPU int8 · beam 1 vs 5'
    write_json(output / 'report/comparison.json', report)
    page = render_comparison(report)
    page = page.replace('本报告根据导入的参考文本和两个版本输出计算。请确认两版使用相同测试集、参考标注和可比的推理设置；工具不验证输入来源。',
                        '真实音频实验：LibriSpeech test-clean，每位说话人按固定哈希规则选一条，共 40 条。相同 Whisper tiny.en 权重、CPU int8，比较 beam 1 与 beam 5。此小样本不代表完整测试集成绩。<br><a href="https://qiangzhang-dev.github.io/notes/whisper-beam/">阅读实验记录、数据来源与复现方法</a>。')
    (output / 'report/index.html').write_text(page, encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT / 'exports/asr-cache')
    parser.add_argument('--output', type=Path, required=True, help='New directory for manifest, raw outputs and report')
    parser.add_argument('--render-only', action='store_true', help='Rebuild an existing results report from saved transcripts; no model or download')
    parser.add_argument('--download', action='store_true', help='Download public dataset (~347 MB) and pinned model (~76 MB)')
    args = parser.parse_args(argv)
    if args.render_only:
        render_experiment_report(args.output.resolve())
        return 0
    protocol_path = ROOT / 'experiments/librispeech-beam/protocol.json'
    protocol = json.loads(protocol_path.read_text())
    cache, output = args.cache.resolve(), args.output.resolve()
    if output.exists():
        parser.error('--output must not exist')
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / 'test-clean.tar.gz'
    model_dir = cache / 'model'
    if args.download:
        if not archive.exists():
            partial = archive.with_suffix('.partial')
            with urllib.request.urlopen(protocol['dataset_url'], timeout=60) as source, partial.open('wb') as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
            if digest(partial) != protocol['dataset_sha256']:
                raise ValueError('Downloaded dataset checksum mismatch')
            partial.rename(archive)
        from huggingface_hub import snapshot_download
        snapshot_download(protocol['model'], revision=protocol['model_revision'], local_dir=str(model_dir),
                          allow_patterns=['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'])
    samples = select_samples(archive, cache, protocol['dataset_sha256'])
    model_hashes = {name: digest(model_dir / name) for name in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt')}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'manifest.json', samples)
    write_json(output / 'protocol.json', protocol)
    environment = {'python': platform.python_version(), 'platform': platform.platform(),
                   'packages': {dist.metadata['Name']: dist.version for dist in importlib.metadata.distributions()},
                   'model_file_sha256': model_hashes, 'protocol_sha256': digest(protocol_path),
                   'manifest_sha256': digest(output / 'manifest.json'),
                   'started_at_utc': datetime.now(timezone.utc).isoformat(),
                   'inference_network_used': False}
    write_json(output / 'environment.json', environment)
    from faster_whisper import WhisperModel
    records = []
    with patch.object(socket.socket, 'connect', side_effect=RuntimeError('Network disabled during inference')):
        model = WhisperModel(str(model_dir), device=protocol['device'], compute_type=protocol['compute_type'],
                             cpu_threads=protocol['cpu_threads'], num_workers=protocol['num_workers'], local_files_only=True)
        with (output / 'raw-results.jsonl').open('w', encoding='utf-8') as raw:
            for index, sample in enumerate(samples):
                record = {'sample_id': sample['sample_id'], 'reference': sample['reference']}
                # Alternate order; latency is observational, not a speed benchmark.
                sides = ('baseline', 'candidate') if index % 2 == 0 else ('candidate', 'baseline')
                for side in sides:
                    start = time.perf_counter()
                    segments, info = model.transcribe(str(cache / 'selected-audio' / (sample['sample_id'] + '.flac')),
                                                     beam_size=protocol[side + '_beam_size'], **protocol['transcribe'])
                    segments = list(segments)
                    record[side] = {'text': ''.join(segment.text for segment in segments).strip(),
                                    'seconds': time.perf_counter() - start, 'audio_seconds': info.duration,
                                    'segments': [{'start': s.start, 'end': s.end, 'text': s.text, 'temperature': s.temperature,
                                                  'avg_logprob': s.avg_logprob} for s in segments]}
                raw.write(json.dumps(record, ensure_ascii=False) + '\n')
                raw.flush()
                records.append(record)
                print(f'{index+1}/{len(samples)} {sample["sample_id"]}', flush=True)
    with (output / 'pairs.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['sample_id', 'reference', 'baseline', 'candidate'])
        writer.writerows([r['sample_id'], r['reference'], r['baseline']['text'], r['candidate']['text']] for r in records)
    report = write_comparison(output / 'pairs.csv', output / 'report', baseline_name='beam 1', candidate_name='beam 5')
    report = render_experiment_report(output)
    summary = {'cer': report['summary'], 'wer': summarize_words(records),
               'audio_seconds': sum(r['baseline']['audio_seconds'] for r in records),
               'transcription_seconds': {side: sum(r[side]['seconds'] for r in records) for side in ('baseline', 'candidate')},
               'outputs_identical': sum(r['baseline']['text'] == r['candidate']['text'] for r in records),
               'finished_at_utc': datetime.now(timezone.utc).isoformat()}
    write_json(output / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
