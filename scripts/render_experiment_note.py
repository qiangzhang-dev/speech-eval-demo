#!/usr/bin/env python3
"""Render the public experiment note (optional dependency: markdown2==2.5.4)."""
from pathlib import Path
import markdown2

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'experiments/librispeech-beam/NOTE.md'
target = ROOT / 'experiments/librispeech-beam/results/index.html'
body = markdown2.markdown(source.read_text(encoding='utf-8'), extras=['tables', 'fenced-code-blocks'])
page = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>beam 从 1 改成 5：40 条录音里，哪些结果变了？ · Nate Zhang</title>
<meta name="description" content="Whisper tiny.en 在 LibriSpeech 40 条真实录音上的解码对比：3 条 CER 改善、37 条不变，附固定协议、原始输出和复现脚本。">
<link rel="canonical" href="https://qiangzhang-dev.github.io/notes/whisper-beam/">
<style>
*{box-sizing:border-box}body{margin:0;background:#fff;color:#30343b;font:17px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:820px;padding:42px 28px 70px;margin:auto}nav{display:flex;flex-wrap:wrap;gap:20px;font-size:14px;margin-bottom:40px}a{color:#1766aa;text-underline-offset:4px}a:focus-visible{outline:2px solid #1766aa;outline-offset:4px}h1{text-wrap:balance;font-size:clamp(28px,5vw,36px);line-height:1.45;letter-spacing:-.025em;color:#181b20;margin:0 0 18px}h1+p{color:#717983;font-size:14px;margin-bottom:32px}h2{font-size:23px;line-height:1.5;margin:36px 0 14px;color:#242a32}p{margin:16px 0}strong{font-weight:600}table{display:block;overflow:auto;border-collapse:collapse;width:100%;font-size:15px;margin:24px 0}th,td{padding:12px 14px;border-bottom:1px solid #e4e8ed;text-align:left;min-width:95px;vertical-align:top}thead{background:#f5f7fa}code{font-size:.88em;background:#f3f5f7;padding:2px 4px;border-radius:3px;overflow-wrap:anywhere}pre{overflow:auto;padding:18px;background:#f3f5f7}pre code{padding:0}ul{padding-left:24px}li{margin:12px 0}footer{border-top:1px solid #e4e8ed;padding-top:20px;margin-top:38px;color:#717983;font-size:13px}@media(max-width:600px){main{padding:26px 18px 50px}body{font-size:16px}nav{margin-bottom:30px}th,td{padding:10px 8px}table{font-size:14px}h2{font-size:21px}}@media print{nav,footer{display:none}main{max-width:none;padding:0}body{font-size:11pt}table{display:table}}
</style></head><body><main><nav aria-label="文章导航"><a href="/">Nate / 个人网站</a><a href="/speech-eval/">评测工具</a><a href="report/">逐句结果</a><a href="https://github.com/qiangzhang-dev/speech-eval-demo/tree/main/experiments/librispeech-beam">复现代码 ↗</a></nav><article>
''' + body + '''</article><footer>Qiang (Nate) Zhang · <a href="/">返回个人网站</a></footer></main></body></html>\n'''
target.write_text(page, encoding='utf-8')
print(target)
