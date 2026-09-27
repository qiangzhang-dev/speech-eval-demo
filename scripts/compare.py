#!/usr/bin/env python3
"""Compare two transcript versions from a paired UTF-8 CSV, entirely offline."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from speech_eval.comparison import write_comparison


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='CSV with sample_id, reference, baseline, candidate')
    parser.add_argument('--output', type=Path, help='New report directory; existing paths are never overwritten')
    parser.add_argument('--baseline-name', default='旧版')
    parser.add_argument('--candidate-name', default='新版')
    args = parser.parse_args(argv)
    try:
        source = args.input.resolve() if args.input else ROOT / 'data/comparison/pairs.csv'
        if args.output:
            output = args.output.resolve()
        else:
            parent = ROOT / 'exports'
            parent.mkdir(exist_ok=True)
            # Reserve a unique parent, then write into a new child directory.
            output = Path(tempfile.mkdtemp(prefix='compare-', dir=parent)) / 'report'
        report = write_comparison(source, output, baseline_name=args.baseline_name,
                                  candidate_name=args.candidate_name, synthetic=args.input is None)
        print(f"Compared {report['summary']['paired']} pairs; excluded {report['summary']['counts']['excluded']}.")
        print(f'Report: {output / "index.html"}')
        return 0
    except (OSError, ValueError, csv.Error) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
