"""Paired transcript comparison using the repository's versioned CER metric."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
from pathlib import Path

from .metrics import METRIC_VERSION, compute_cer
from .normalization import NORMALIZATION_VERSION, normalize_text

REQUIRED = ('sample_id', 'reference', 'baseline', 'candidate')
STATUS = {'improved': 'CER 降低', 'regressed': 'CER 升高', 'unchanged': 'CER 不变', 'excluded': '未计入'}


def load_pairs(path: Path):
    """Require explicit pairs; empty output is valid, missing CSV cells are not."""
    raw = path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not set(REQUIRED).issubset(fields):
        raise ValueError('CSV needs unique columns: sample_id, reference, baseline, candidate')
    rows, seen = [], set()
    for number, row in enumerate(reader, 2):
        if None in row or any(row[key] is None for key in REQUIRED):
            raise ValueError(f'CSV record {number}: missing or extra cells')
        sample_id = row['sample_id'].strip()
        if not sample_id or sample_id in seen:
            raise ValueError(f'CSV record {number}: empty or duplicate sample_id')
        seen.add(sample_id)
        if any(len(row[key]) > 4000 or len(normalize_text(row[key])) > 1000 for key in REQUIRED[1:]):
            raise ValueError(f'CSV record {number}: split long transcripts (max 1000 normalized characters per field)')
        rows.append({**{key: row[key] for key in REQUIRED}, 'sample_id': sample_id})
    if not rows:
        raise ValueError('CSV contains no samples')
    return rows, hashlib.sha256(raw).hexdigest()


def compare_pairs(rows):
    results = []
    counts = dict.fromkeys(STATUS, 0)
    for row in rows:
        baseline = compute_cer(row['reference'], row['baseline']).to_dict()
        candidate = compute_cer(row['reference'], row['candidate']).to_dict()
        delta = None if baseline['value'] is None else candidate['value'] - baseline['value']
        status = 'excluded' if delta is None else 'improved' if delta < 0 else 'regressed' if delta > 0 else 'unchanged'
        counts[status] += 1
        results.append({**row, 'baseline_metric': baseline, 'candidate_metric': candidate,
                        'delta': delta, 'status': status})
    applicable = [r for r in results if r['status'] != 'excluded']
    reference_characters = sum(r['baseline_metric']['reference_length'] for r in applicable)
    summary = {'total': len(results), 'paired': len(applicable), 'counts': counts,
               'reference_characters': reference_characters,
               'metric_version': METRIC_VERSION, 'normalization_version': NORMALIZATION_VERSION}
    for side in ('baseline', 'candidate'):
        metrics = [r[f'{side}_metric'] for r in applicable]
        errors = sum(m['substitutions'] + m['deletions'] + m['insertions'] for m in metrics)
        summary[side] = {'macro_cer': sum(m['value'] for m in metrics) / len(metrics) if metrics else None,
                         'corpus_cer': errors / reference_characters if reference_characters else None,
                         'character_errors': errors}
    summary['delta'] = {key: summary['candidate'][key] - summary['baseline'][key] if applicable else None
                        for key in ('macro_cer', 'corpus_cer')}
    return {'summary': summary, 'samples': results}


def pct(value):
    return '不适用' if value is None else f'{value:.2%}'


def pp(value):
    return '不适用' if value is None else f'{value * 100:+.2f} 个百分点'


def render_comparison(report):
    esc = lambda value: html.escape(str(value), quote=True)
    summary, provenance = report['summary'], report['provenance']
    baseline_name, candidate_name = esc(provenance['baseline_name']), esc(provenance['candidate_name'])
    cards = []
    for row in report['samples']:
        sections = []
        for side, name in (('baseline', baseline_name), ('candidate', candidate_name)):
            metric = row[f'{side}_metric']
            edits = ''.join(f'<tr><td>{esc(op["op"])}</td><td>{esc(op["reference"])}</td><td>{esc(op["hypothesis"])}</td></tr>' for op in metric['operations'])
            if not edits:
                edits = '<tr><td colspan="3">' + ('参考文本归一化后为空，未计算编辑对齐。' if not metric['applicable'] else '归一化后没有字符差异。') + '</td></tr>'
            word = row.get(side + '_word_metric')
            word_label = f' · WER {pct(word["wer"])}' if word else ''
            word_detail = ''
            if word:
                word_detail = f'<p class="muted">词编辑：{word["errors"]} / {word["reference_words"]} 个参考词</p><details><summary>查看分词</summary><p class="muted">参考：{esc(" | ".join(word["reference_tokens"]))}<br>输出：{esc(" | ".join(word["hypothesis_tokens"]))}</p></details>'
            sections.append(f'<section><h4>{name} · CER {pct(metric["value"])}{word_label}</h4>{word_detail}<p class="transcript">{esc(row[side]) or "（空输出）"}</p><details><summary>查看字符差异</summary><p class="muted">归一化参考：{esc(metric["normalized_reference"])}<br>归一化输出：{esc(metric["normalized_hypothesis"])}</p><table><thead><tr><th>操作</th><th>参考</th><th>输出</th></tr></thead><tbody>{edits}</tbody></table></details></section>')
        blindspot = any(row[f'{side}_metric']['value'] == 0 and row.get(side + '_word_metric', {}).get('errors', 0) > 0 for side in ('baseline', 'candidate'))
        cards.append(f'<article class="case" data-word-error="{str(blindspot).lower()}" data-status="{row["status"]}"><div class="case-head"><h3>{esc(row["sample_id"])}</h3><span class="badge {row["status"]}">{STATUS[row["status"]]} · {pp(row["delta"])}</span></div><p class="label">参考文本</p><p class="transcript">{esc(row["reference"]) or "（空参考）"}</p><div class="pair">{"".join(sections)}</div></article>')
    notice = '这里的两个版本均为人工设置的合成转写文本，用来演示改善与退步；没有运行真实语音模型，分数不代表模型表现。' if provenance['synthetic'] else '本报告根据导入的参考文本和两个版本输出计算。请确认两版使用相同测试集、参考标注和可比的推理设置；工具不验证输入来源。'
    stats = ''.join(f'<div class="stat"><strong>{summary["counts"][key]}</strong><span>{label}</span></div>' for key, label in STATUS.items())
    metric_rows = ''.join(f'<tr><th>{label}</th><td>{pct(summary["baseline"][key])}</td><td>{pct(summary["candidate"][key])}</td><td>{pp(summary["delta"][key])}</td></tr>' for key, label in (('macro_cer', '样例平均 CER'), ('corpus_cer', '语料级 CER')))
    word_notice = word_option = ''
    if 'word_summary' in report:
        words = report['word_summary']
        metric_rows += f'<tr><th>语料级 WER（本文分词规则）</th><td>{pct(words["baseline"]["corpus_wer"])}</td><td>{pct(words["candidate"]["corpus_wer"])}</td><td>{pp(words["candidate"]["corpus_wer"] - words["baseline"]["corpus_wer"])}</td></tr>'
        word_notice = '<p class="notice">CER 去掉空格，可能漏掉单词分界错误。选择下方「CER 为 0、WER 大于 0」可找到字符指标未体现的词级差异，展开「查看分词」核对原因。差异不一定是识别错误：参考中的 TO-DAY 与输出 today 也会被当前分词规则计错。WER 按 NFKC、casefold 和保留词内撇号的英文词与数字计算，不展开数字或缩写；不是 LibriSpeech 官方评分流程。重新导入 CSV 的通用工具仅计算 CER。</p>'
        word_option = '<option value="word-error">CER 为 0、WER 大于 0（任一版本）</option>'
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>转写版本对比 · Nate Zhang</title>
<style>
:root{color-scheme:light;--ink:#202a35;--muted:#627080;--line:#dfe5eb;--accent:#17665a}*{box-sizing:border-box}body{margin:0;background:#f7f8fa;color:var(--ink);font:16px/1.75 system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:1050px;margin:auto;padding:40px 24px 64px}a{color:var(--accent);text-underline-offset:4px}nav{display:flex;flex-wrap:wrap;gap:20px;font-size:14px;margin-bottom:44px}h1{font-size:clamp(28px,5vw,42px);line-height:1.3;letter-spacing:-.035em;margin:12px 0 18px}h2{font-size:24px;margin:36px 0 14px}h3,h4{margin:0;font-size:17px}p{margin:10px 0}.eyebrow,.label{font-size:12px;color:var(--accent);font-weight:600}.muted{color:var(--muted);font-size:14px}.notice{border-left:3px solid #bf7433;padding:12px 18px;background:#fff6e9;margin:24px 0}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat{background:white;border:1px solid var(--line);border-radius:12px;padding:18px}.stat strong{display:block;font-size:30px;font-weight:600}.stat span{font-size:13px;color:var(--muted)}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:14px}th,td{padding:12px;border-bottom:1px solid var(--line)}.case{background:white;border:1px solid var(--line);border-radius:14px;padding:24px;margin:18px 0}.case-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px}.badge{border-radius:4px;background:#edf0f3;padding:3px 10px;font-size:13px}.improved{background:#e7f3eb;color:#236744}.regressed{background:#fff0e5;color:#96451d}.pair{display:grid;grid-template-columns:1fr 1fr;gap:26px;margin-top:20px;padding-top:18px;border-top:1px solid var(--line)}.pair section{min-width:0}.transcript{white-space:pre-wrap;overflow-wrap:anywhere}.muted{overflow-wrap:anywhere}summary{color:var(--accent);cursor:pointer;font-size:14px;margin-top:14px}pre{overflow:auto;border-radius:8px;background:#eaf0f3;padding:18px;font-size:14px}select,input{font:inherit;padding:8px;border:1px solid #bdc8d1;border-radius:6px;background:white;max-width:100%}.filters{display:flex;gap:18px;flex-wrap:wrap;align-items:end}.filters label{display:flex;flex-direction:column;font-size:14px;gap:5px}a:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #bf7433;outline-offset:3px}footer{border-top:1px solid var(--line);margin-top:38px;padding-top:18px;color:var(--muted);font-size:13px}[hidden]{display:none!important}@media(max-width:620px){main{padding:24px 16px 40px}.stats{grid-template-columns:repeat(2,1fr)}.pair{grid-template-columns:1fr;gap:20px}.case{padding:18px}.stat{padding:14px}th,td{padding:8px}nav{margin-bottom:30px}}@media print{.filters,nav{display:none}.case{break-inside:avoid}body{background:white}}
</style></head><body><main>
<nav><a href="https://qiangzhang-dev.github.io/">Nate / 个人网站</a><a href="https://qiangzhang-dev.github.io/speech-eval/">单版本结果</a><a href="https://github.com/qiangzhang-dev/speech-eval-demo">源代码 ↗</a></nav>
<header><span class="eyebrow">SPEECH EVALUATION / COMPARE</span><h1>换了版本，哪些句子变了？</h1><p>对照同一份参考文本，查看两个版本的字符错误率与具体差异。</p></header>
''' + f'<p class="notice">{notice}</p><div class="stats">{stats}</div><p class="muted">共 {summary["total"]} 条；{summary["paired"]} 条纳入配对统计。归一化后为空的参考文本不计入任何均值。</p><div class="table-wrap"><table><thead><tr><th>计算口径</th><th>{baseline_name}</th><th>{candidate_name}</th><th>新版 − 旧版</th></tr></thead><tbody>{metric_rows}</tbody></table></div>' + '''
<p class="muted">样例平均 CER 对每句等权；语料级 CER = 全部字符编辑次数 / 全部参考字符数。差值为负表示 CER 降低；CER 可以超过 100%。同分可能对应不同错误，不代表语义或任务效果相同。</p>
''' + word_notice + '''<h2>逐条对照</h2><div class="filters" id="filters" hidden><label>筛选<select id="status"><option value="all">全部样例</option><option value="regressed">CER 升高</option><option value="improved">CER 降低</option><option value="unchanged">CER 不变</option><option value="excluded">未计入</option>''' + word_option + '''</select></label><label>查找样例<input id="search" type="search" placeholder="样例 ID 或原文"></label></div><p id="visible-count" class="muted" role="status" aria-live="polite"></p>
''' + ''.join(cards) + '''
<section><h2>换成自己的结果</h2><p>下载 <a href="input.csv" download>本页输入 CSV</a>，替换四列：<code>sample_id</code>、<code>reference</code>、<code>baseline</code>、<code>candidate</code>。一行对应同一个样例，输出为空时保留空单元格。保存为 UTF-8 CSV，在仓库中运行：</p>
<pre><code>python scripts/compare.py --input your-pairs.csv --output exports/my-comparison</code></pre>
<p>用浏览器打开输出目录的 <code>index.html</code> 即可筛选。计算在本地完成，无需上传数据或配置模型密钥。输出目录必须尚不存在。</p><p><a href="comparison.json" download>完整对比 JSON</a> · <a href="comparison.csv" download>汇总 CSV</a></p>
<h2>计算范围</h2><p class="muted">沿用 cer-v1 / cer-normalize-v1：NFKC、casefold，仅保留汉字、拉丁字母和十进制数字。这里只比较转写文本，不评估音频、语义、翻译、意图或动作，也不设置质量通过门槛。不支持该归一化范围之外的通用多语种评测。</p>
''' + f'<p class="muted">输入 SHA-256：<code>{esc(provenance["input_sha256"])}</code>。完整结果保留每处替换、删除和插入。</p></section><footer>Independent project by Qiang (Nate) Zhang · {"Synthetic examples only." if provenance["synthetic"] else "Local transcript comparison."}</footer>' + '''
</main><script>
const filters=document.getElementById('filters'),status=document.getElementById('status'),search=document.getElementById('search'),cards=[...document.querySelectorAll('.case')];
filters.hidden=false;
function update(){let count=0;const query=search.value.trim().toLocaleLowerCase();for(const card of cards){const visible=(status.value==='all'||card.dataset.status===status.value||(status.value==='word-error'&&card.dataset.wordError==='true'))&&card.textContent.toLocaleLowerCase().includes(query);card.hidden=!visible;if(visible)count++;}document.getElementById('visible-count').textContent=`显示 ${count} / ${cards.length} 条样例`;}
status.addEventListener('change',update);search.addEventListener('input',update);update();
</script></body></html>\n'''


def spreadsheet_text(value):
    """Keep untrusted transcript text from being interpreted as a CSV formula."""
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


def write_comparison(input_path, output, *, baseline_name='旧版', candidate_name='新版', synthetic=False):
    rows, digest = load_pairs(input_path)
    report = compare_pairs(rows)
    report['provenance'] = {'input_sha256': digest, 'baseline_name': baseline_name,
                            'candidate_name': candidate_name, 'synthetic': synthetic, 'network_used': False}
    # Validate and calculate before creating the output. Never overwrite a report.
    output.mkdir(parents=True, exist_ok=False)
    (output / 'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'index.html').write_text(render_comparison(report), encoding='utf-8')
    (output / 'input.csv').write_bytes(input_path.read_bytes())
    with (output / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow([*REQUIRED, 'status', 'baseline_cer', 'candidate_cer', 'delta_pp'])
        for row in report['samples']:
            writer.writerow([*(spreadsheet_text(row[key]) for key in REQUIRED), row['status'],
                             row['baseline_metric']['value'], row['candidate_metric']['value'],
                             None if row['delta'] is None else row['delta'] * 100])
    return report
