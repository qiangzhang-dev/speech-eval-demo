# speech-eval-demo

一个可离线运行的多场景语音评测与问题分析 Demo，提供样例校验、CER 指标、证据约束诊断、受控修订、SQLite 持久化、JSONL/CSV 导出和本地 Web 工作台。

**[浏览六条样例的结果](https://qiangzhang-dev.github.io/speech-eval/)** · [下载示例 JSONL](docs/demo/evaluation-results.jsonl) · [架构图](docs/architecture.svg)

这是个人工程项目。演示使用预设的合成参考文本和识别输出，不调用 ASR/LLM，也不运行真实音频识别。它展示如何把评分、错误证据和复核过程串起来。

## 一条命令运行

需要 **Python 3.11+**；核心仅依赖标准库，无需 pip 安装、API 密钥或 GPU。以下命令适用于 Windows PowerShell、macOS 和 Linux（后两者也可用 `python3`）：

```sh
git clone https://github.com/qiangzhang-dev/speech-eval-demo.git
cd speech-eval-demo
python scripts/demo.py
```

程序会校验并处理 `data/fixtures` 中的六条样例，打印报告与数据库的绝对路径。双击生成的 `index.html`，即可离线查看每条样例的 CER、参考/输出对照、字符编辑证据及下游阶段。

每次默认生成独立的 `exports/demo-*` 目录，避免重复运行污染统计。也可以指定一个**尚不存在**的目录：

```sh
python scripts/demo.py --output exports/my-first-demo
```

| 输出 | 用途 |
| --- | --- |
| `index.html` | 可筛选、可展开详情的自包含结果页 |
| `evaluation-results.jsonl` / `.csv` | 六条完整结果与证据 |
| `summary.json` | 汇总、版本、输入与配置 SHA-256 |
| `evaluation.db` | 支持后续查询与修订的 SQLite 数据库 |

## 看懂这次结果

以下数据来自上述命令对仓库合成样例的实际运行，**不是模型性能或真实业务指标**。

| 样例 | 合成场景 | CER |
| --- | --- | ---: |
| SYN-0001 | 普通话口述 | 0.00% |
| SYN-0002 | 噪声/数字英文混合 | 6.25% |
| SYN-0003 | 短句语翻 | 25.00% |
| SYN-0004 | 长句/上下文翻译 | 94.74% |
| SYN-0005 | 设备控制 | 0.00% |
| SYN-0006 | 信息查询/多轮任务 | 8.33% |

六条样例全部处理完成，样例平均 CER 为 **22.39%**。这是逐样例等权宏平均，不是按参考字符数加权的语料级 CER。

例如 SYN-0002 的参考为“请把订单 A1007 的数量改成 3 份”，预设输出漏掉了“份”。归一化后参考为 16 个字符，1 次删除得到 `CER = 1/16 = 6.25%`。展开页面中的证据即可定位这个字符；低 CER 不保证关键语义完整。

计算口径：`cer-v1` 使用字符级 Levenshtein 距离；`cer-normalize-v1` 先 NFKC/casefold，再只保留汉字、拉丁字母和十进制数字。下游阶段使用字段精确匹配，不等同于语义裁判。仓库阈值仍是未生效的候选配置，因此“运行完成”不意味着“质量通过”。

公开预览与 [docs/demo](docs/demo) 使用同一批运行结果。重跑的指标与 JSONL 应一致，生成时间、Python 版本和本地目录可以不同。

## 本地交互工作台

```sh
python scripts/demo.py --serve
```

打开 `http://127.0.0.1:8000/`：

1. 先看六条结果，再按场景或结论筛选。
2. 打开 SYN-0002，对照参考、输出和字符删除证据。
3. 查看日志；需要人工调整时填写理由，修订会保留审计记录。
4. 导出 JSONL/CSV 继续分析。按 Ctrl+C 停止服务。

端口占用时可加 `--port 8001`。服务默认只监听本机。线上预览是静态结果页，完整工作台在本地运行。

## 更多样例与原始 CLI

如需使用已有的较大合成集或自己的清单，先配置模块路径：

```powershell
# Windows PowerShell
$env:PYTHONPATH = "src"
```

```sh
# macOS / Linux
export PYTHONPATH=src
```

然后运行：

```sh
python -m speech_eval validate data/generated_verify3/manifest.csv
python -m speech_eval run data/generated_verify3/manifest.csv --database var/evaluation.db --provider rules
python -m speech_eval export --database var/evaluation.db --output exports/evaluation
python scripts/generate_report.py --database var/evaluation.db --output exports/evaluation/evaluation-summary.html
python -m speech_eval serve --database var/evaluation.db --host 127.0.0.1 --port 8000
```

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
docs/         公开架构图与六条合成样例结果预览
schemas/      结果 Schema
scripts/      生成、报告、审计和验收工具
src/          核心实现
tests/        自动化测试
web/          本地工作台
```

## 数据边界

仓库中的 `data/fixtures` 和 `data/generated_verify3` 是合成工程样例，仅用于演示和可复现测试，不代表真实用户数据、真实业务效果或正式业务验收。任何真实数据接入、阈值审批、人工抽检和在线发布都应由相应责任人按组织流程完成。
