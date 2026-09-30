#!/usr/bin/env python3
"""Compare two transcript versions from a paired UTF-8 CSV, entirely offline."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys
import tempfile
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from speech_eval.comparison import load_pairs, write_comparison
from speech_eval.normalization import normalize_text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='CSV with sample_id, reference, baseline, candidate')
    parser.add_argument('--output', type=Path, help='New report directory; existing paths are never overwritten')
    parser.add_argument('--baseline-name', default='旧版')
    parser.add_argument('--candidate-name', default='新版')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Validate the CSV without creating a report')
    mode.add_argument('--open', action='store_true', help='Open the completed local report in your default browser')
    args = parser.parse_args(argv)
    if args.check and args.output:
        parser.error('--check does not create files; omit --output')
    try:
        source = args.input.resolve() if args.input else ROOT / 'data/comparison/pairs.csv'
        rows, _ = load_pairs(source)
        if args.check:
            excluded = sum(not normalize_text(row['reference']) for row in rows)
            print(f'CSV OK: {len(rows)} samples; {excluded} empty normalized references excluded from scores.')
            print('No report files created. Remove --check to generate a report.')
            return 0
        if args.output:
            output = args.output.resolve()
        else:
            parent = ROOT / 'exports'
            parent.mkdir(exist_ok=True)
            # Reserve a unique parent, then write into a new child directory.
            output = Path(tempfile.mkdtemp(prefix='compare-', dir=parent)) / 'report'
        report = write_comparison(source, output, baseline_name=args.baseline_name,
                                  candidate_name=args.candidate_name, synthetic=args.input is None,
                                  canonical_url='https://qiangzhang-dev.github.io/speech-eval/compare/'
                                  if args.input is None else None)
        print(f"Compared {report['summary']['paired']} pairs; excluded {report['summary']['counts']['excluded']}.")
        page = output / 'index.html'
        print(f'Report: {page}')
        print(f'Open in a browser: {page.as_uri()}')
        if args.open:
            try:
                opened = webbrowser.open(page.as_uri())
            except webbrowser.Error:
                opened = False
            if not opened:
                print('Could not open a browser automatically. The report is saved; open the path above.')
        return 0
    except (OSError, ValueError, csv.Error) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        if isinstance(exc, FileNotFoundError):
            print('Check --input; quote paths containing spaces. Start from data/comparison/starter.csv.', file=sys.stderr)
        elif isinstance(exc, FileExistsError):
            print('Choose a new --output directory, or omit --output for a unique directory. Existing files were not overwritten.', file=sys.stderr)
        elif isinstance(exc, UnicodeError):
            print('Save as CSV UTF-8 (comma delimited), not XLSX or a legacy CSV encoding.', file=sys.stderr)
        elif isinstance(exc, (ValueError, csv.Error)):
            print('Use sample_id,reference,baseline,candidate; each ID must be unique. See the CSV troubleshooting section in README.md.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
