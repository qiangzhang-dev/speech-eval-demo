#!/usr/bin/env python3
"""Run the six bundled synthetic cases offline and build a browsable report."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from speech_eval.batch import BatchProcessor
from speech_eval.config import load_threshold_config
from speech_eval.exporters import export_repository
from speech_eval.manifest import load_manifest
from speech_eval.reporting import build_summary
from speech_eval.repository import EvaluationRepository
from speech_eval.web import serve


def escape(value):
    return html.escape(str(value), quote=True)


def render_report(results, summary):
    cards = []
    for result in results:
        metric = next(e['content'] for e in result.evidence if e['type'] == 'metric_snapshot')
        edits = [e for e in result.evidence if e['type'] == 'text_diff']
        edit_rows = ''.join(
            '<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
                escape(e['content']['op']), escape(e['content']['reference']),
                escape(e['content']['hypothesis']), escape(e['evidence_id'])) for e in edits)
        if not edit_rows:
            edit_rows = '<tr><td colspan="4">归一化后没有字符差异。</td></tr>'
        stages = result.quantitative_metrics.get('stage_metrics', [])
        stage_rows = ''.join('<li>{}：{}<br>参考：{}<br>输出：{}</li>'.format(
            escape(s.get('stage')), '一致' if s.get('matched') is True else
            ('不一致' if s.get('matched') is False else '不适用'),
            escape(json.dumps(s.get('reference'), ensure_ascii=False)),
            escape(json.dumps(s.get('hypothesis'), ensure_ascii=False))) for s in stages)
        cards.append(f'''<article class="case" data-error="{str(bool(edits)).lower()}">
<div class="case-head"><div><span class="eyebrow">{escape(result.sample_id)}</span>
<h3>{escape(result.subscene_type)}</h3><p class="muted">{escape(result.scene_type)}</p></div>
<strong class="score">{metric['value']:.2%}<small>CER</small></strong></div>
<div class="comparison"><div><span class="label">参考文本</span><p>{escape(result.reference_annotation['transcript'])}</p></div>
<div><span class="label">识别输出 · 预设合成文本</span><p>{escape(result.system_output['transcript'])}</p></div></div>
<p class="formula">({metric['substitutions']} 替换 + {metric['deletions']} 删除 + {metric['insertions']} 插入) / {metric['reference_length']} 参考字符</p>
<details><summary>查看字符差异与下游阶段</summary>
<p class="muted">归一化参考：{escape(metric['normalized_reference'])}<br>归一化输出：{escape(metric['normalized_hypothesis'])}</p>
<div class="table-wrap"><table><thead><tr><th>操作</th><th>参考</th><th>输出</th><th>证据 ID</th></tr></thead><tbody>{edit_rows}</tbody></table></div>
{('<h4>下游阶段 · 精确匹配</h4><ul>' + stage_rows + '</ul>') if stages else '<p>本样例没有适用的下游阶段。</p>'}
</details></article>''')
    stats = summary['metric_summary']
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="六条合成语音转写样例的可复现离线评测：CER、字符差异和下游阶段对照。">
<title>Speech Evaluation Demo · 语音转写评测示例</title>
<style>
:root{color-scheme:light;--ink:#202a35;--muted:#627080;--line:#dfe5eb;--accent:#17665a}*{box-sizing:border-box}body{margin:0;background:#f7f8fa;color:var(--ink);font:16px/1.75 system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:1000px;margin:auto;padding:40px 24px 64px}a{color:var(--accent);text-underline-offset:4px}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #bf7433;outline-offset:4px}nav{display:flex;flex-wrap:wrap;gap:18px;font-size:14px;margin-bottom:50px}.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--accent);font-weight:700}h1{font-size:clamp(28px,5vw,42px);letter-spacing:-.04em;line-height:1.3;margin:12px 0 18px}h2{font-size:23px;margin:34px 0 14px}h3{font-size:20px;margin:4px 0}h4{margin-bottom:8px}p{margin:8px 0}.intro{max-width:740px}.muted{color:var(--muted);font-size:14px}.notice{border-left:3px solid #bf7433;padding:12px 18px;background:#fff6e9;margin:24px 0}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.stat{background:white;border:1px solid var(--line);border-radius:12px;padding:18px}.stat strong{display:block;font-size:30px;font-weight:600}.stat span{font-size:13px;color:var(--muted)}.case{background:white;border:1px solid var(--line);border-radius:14px;padding:24px;margin:18px 0}.case-head{display:flex;justify-content:space-between;gap:20px}.score{font-size:27px;white-space:nowrap;text-align:right}.score small{display:block;font-size:12px;font-weight:400;color:var(--muted)}.comparison{display:grid;grid-template-columns:1fr 1fr;gap:24px;padding:18px 0;margin:10px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.comparison p{overflow-wrap:anywhere}.label{font-size:12px;color:var(--muted)}.formula{font-size:14px;color:var(--muted)}summary{cursor:pointer;padding:10px 0;color:var(--accent)}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px;text-align:left}th,td{padding:9px;border-bottom:1px solid var(--line)}pre{background:#eaf0f3;padding:18px;overflow:auto;border-radius:8px;font-size:14px}.downloads{display:flex;flex-wrap:wrap;gap:18px}button{background:white;color:var(--accent);border:1px solid var(--accent);padding:10px 14px;border-radius:6px;font:inherit;cursor:pointer}footer{border-top:1px solid var(--line);margin-top:38px;padding-top:18px;font-size:13px;color:var(--muted)}li{margin-bottom:8px;overflow-wrap:anywhere}[hidden]{display:none!important}@media(max-width:600px){main{padding:24px 16px 40px}nav{margin-bottom:32px}.stats{gap:8px}.stat{padding:12px 10px}.stat strong{font-size:23px}.case{padding:18px}.comparison{grid-template-columns:1fr;gap:12px}.score{font-size:23px}h3{font-size:18px}}@media print{body{background:white}nav,button{display:none}.case{break-inside:avoid}}
</style></head><body><main>
<nav aria-label="项目导航"><a href="https://qiangzhang-dev.github.io/">Nate / 个人网站</a><a href="https://github.com/qiangzhang-dev/speech-eval-demo">源代码与使用说明 ↗</a><a href="#reproduce">本地复现 ↓</a></nav>
<header><span class="eyebrow">SPEECH EVALUATION DEMO</span><h1>语音转写评测示例</h1>
<p class="intro">这里展示六条合成样例的评测结果。可以对照参考文本和识别输出，展开查看每处字符差异，以及翻译、意图和动作等字段是否一致。</p></header>
<p class="notice">这是<strong>合成文本样例的工程演示</strong>。本次运行没有处理真实录音、调用 ASR 或 LLM；识别输出是预设数据。数字仅展示工具行为，不代表模型或业务效果。</p>
''' + f'''<div class="stats"><div class="stat"><strong>{len(results)}</strong><span>已处理合成样例</span></div>
<div class="stat"><strong>{stats['mean']:.2%}</strong><span>样例平均 CER · 宏平均</span></div>
<div class="stat"><strong>{sum(bool([e for e in r.evidence if e['type'] == 'text_diff']) for r in results)}</strong><span>包含字符差异的样例</span></div></div>''' + '''
<p class="muted">CER = (替换 + 删除 + 插入) / 参考字符数。上方对每条样例的 CER 等权平均，不是按参考字符数加权的语料级 CER，也不是语义准确率。</p>
<h2>样例与字符差异</h2><p>展开样例查看字符编辑证据。CER 较低也可能漏掉关键实体；下游精确匹配只描述字段一致性。</p>
<button id="filter" type="button" aria-pressed="false" hidden>只看有字符差异的样例</button>
''' + '\n'.join(cards) + '''
<section id="reproduce"><h2>在你的电脑上复现</h2><p>Python 3.11+，仅使用标准库。无需账号、密钥或额外安装包。</p>
<pre><code>git clone https://github.com/qiangzhang-dev/speech-eval-demo.git
cd speech-eval-demo
python scripts/demo.py</code></pre>
<p>命令会打印本次报告和 SQLite 数据库的位置。需要筛选、人工修订与完整诊断工作台时：</p>
<pre><code>python scripts/demo.py --serve</code></pre>
<p>打开 <code>http://127.0.0.1:8000/</code>，按 Ctrl+C 停止。本页是静态结果预览，完整工作台在本地运行。</p>
<h2>结果与计算口径</h2><p class="muted">本例的质量阈值尚未生效，因此没有给出正式通过或失败的判定。</p><div class="downloads"><a href="evaluation-results.jsonl" download>完整 JSONL</a><a href="evaluation-results.csv" download>完整 CSV</a><a href="summary.json" download>汇总 JSON</a></div>
<p class="muted">数据：data/fixtures/manifest.csv · 诊断：离线 rules · 指标：cer-v1 · 归一化：cer-normalize-v1（NFKC、casefold，仅保留汉字、拉丁字母及十进制数字）。不适用于所有语言的通用评测。</p>
<p class="muted">输入文件与阈值配置的 SHA-256 记录在 summary.json 中。重复运行的指标与样例输出应一致；时间戳与输出目录可以不同。</p></section>
<footer>Independent project by Qiang (Nate) Zhang · Synthetic examples only.</footer>
</main><script>
const filter = document.getElementById('filter'); filter.hidden = false;
filter.addEventListener('click', () => { const active = filter.getAttribute('aria-pressed') !== 'true'; filter.setAttribute('aria-pressed', String(active)); filter.textContent = active ? '显示全部样例' : '只看有字符差异的样例'; document.querySelectorAll('.case').forEach(card => { card.hidden = active && card.dataset.error !== 'true'; }); });
</script></body></html>\n'''


def run_demo(output):
    manifest = ROOT / 'data/fixtures/manifest.csv'
    thresholds_path = ROOT / 'config/thresholds.json'
    samples = load_manifest(manifest, check_paths=True)
    config = load_threshold_config(thresholds_path)
    database = output / 'evaluation.db'
    with EvaluationRepository(database) as repository:
        run = BatchProcessor(repository, thresholds={k: config[k] for k in ('pass_max', 'attention_max')},
            threshold_version=config['threshold_version'], threshold_approval_status=config['approval_status'],
            threshold_effective=config['effective']).run(samples, batch_id='synthetic-demo', run_id='synthetic-demo')
        if run.failed:
            raise ValueError(f'{run.failed} samples failed; inspect {database}')
        export_repository(repository, output, run_id=run.run_id)
        results = repository.list_results(run_id=run.run_id)
        summary = build_summary(repository, run_id=run.run_id, threshold_config=config)
        inputs = [manifest, thresholds_path, *sorted((manifest.parent / 'inputs').glob('*.json'))]
        summary['reproduction'] = {'command': 'python scripts/demo.py', 'provider': 'rules',
            'network_used': False, 'python_version': sys.version.split()[0],
            'input_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (output / 'index.html').write_text(render_report(results, summary), encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New output directory; existing paths are never overwritten')
    parser.add_argument('--serve', action='store_true', help='Start the local workbench after generating results')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    try:
        if args.output:
            output = args.output.resolve()
            output.mkdir(parents=True, exist_ok=False)
        else:
            parent = ROOT / 'exports'
            parent.mkdir(exist_ok=True)
            output = Path(tempfile.mkdtemp(prefix='demo-', dir=parent))
        summary = run_demo(output)
        print(f"Completed {summary['counts']['completed']} synthetic examples (offline rules).")
        print(f"Mean sample CER: {summary['metric_summary']['mean']:.6f}")
        print(f'Report: {output / "index.html"}')
        print(f'Database: {output / "evaluation.db"}')
        if args.serve:
            print(f'Workbench: http://127.0.0.1:{args.port}/ (Ctrl+C to stop)', flush=True)
            serve(str(output / 'evaluation.db'), host='127.0.0.1', port=args.port, static_dir=str(ROOT / 'web'))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
