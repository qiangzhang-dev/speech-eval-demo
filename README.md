# speech-eval-demo

一个可离线运行的多场景语音评测与问题分析 Demo，提供样例校验、CER 指标、证据约束诊断、受控修订、SQLite 持久化、JSONL/CSV 导出和本地 Web 工作台。

## 环境

- Python 3.11+
- 核心运行仅依赖标准库

```powershell
$env:PYTHONPATH = "src"
```

## 快速运行

校验合成样例：

```powershell
python -m speech_eval validate data\generated_verify3\manifest.csv
```

运行离线规则诊断并导出：

```powershell
python -m speech_eval run data\generated_verify3\manifest.csv --database var\evaluation.db --provider rules
python -m speech_eval export --database var\evaluation.db --output exports\evaluation
python scripts\generate_report.py --database var\evaluation.db --output exports\evaluation\evaluation-summary.html
python -m speech_eval serve --database var\evaluation.db --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/` 可查看统计、筛选、详情、证据、日志、受约束修订和 JSON/CSV 下载。

浏览器录制脚本是可选工具，不参与核心离线测试；需要 Node.js、Playwright 和可用的 Chromium/Edge。默认从项目安装加载 Playwright，也可通过 `SPEECH_EVAL_PLAYWRIGHT_MODULE` 指定模块路径、通过 `SPEECH_EVAL_BROWSER_EXECUTABLE` 指定浏览器可执行文件。Windows 提权脚本可通过 `SPEECH_EVAL_NODE_EXE` 指定 `node.exe`。

在线 Provider 只有在显式允许网络时才会启用，密钥仅从环境变量读取：

```powershell
$env:SPEECH_EVAL_API_KEY = "<AUTHORIZED_KEY>"
python -m speech_eval run data\generated_verify3\manifest.csv --database var\ai-evaluation.db --provider openai-compatible --model <MODEL> --api-base <OPENAI_COMPATIBLE_BASE> --api-key-env SPEECH_EVAL_API_KEY --allow-network
```

## 测试

```powershell
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
python -X utf8 -m unittest discover -s tests -v
```

## 目录

```text
config/       评测计划、阈值和 Prompt
data/         合成样例清单及 JSON 输入
docs/          公开架构图（本地证据和交付材料不纳入公开版）
schemas/      结果 Schema
scripts/      生成、报告、审计和验收工具
src/          核心实现
tests/        自动化测试
web/          本地工作台
```

## 数据边界

仓库中的 `data/fixtures` 和 `data/generated_verify3` 是合成工程样例，仅用于演示和可复现测试，不代表真实用户数据、真实业务效果或正式业务验收。任何真实数据接入、阈值审批、人工抽检和在线发布都应由相应责任人按组织流程完成。
