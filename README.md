# speech-eval-demo

把参考文本和识别结果放在一起，计算字符错误率（CER），再看具体漏了、错了或多了哪些字。结果可以在本地网页里筛选、复核，也可以导出为 CSV 或 JSONL。

**[浏览六条样例的结果](https://qiangzhang-dev.github.io/speech-eval/)** · [下载示例 JSONL](docs/demo/evaluation-results.jsonl) · [架构图](docs/architecture.svg)

**[对比两个版本的转写](https://qiangzhang-dev.github.io/speech-eval/compare/)**：筛出 CER 升高的句子，查看旧版和新版分别错在哪里。

演示包含六条合成文本样例。识别输出是预设的，不调用 ASR 或 LLM；页面中的分数用于检查工具行为，不是模型测评成绩。

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

运行上面的命令，会得到以下结果：

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

## 对比两个版本，或导入自己的转写

```sh
python scripts/compare.py
```

这会生成另一份离线报告。默认输入是 [六条配对合成文本](data/comparison/pairs.csv)：新版刻意修复了两条、改坏了两条，另外两条保留原样。两个版本都是预设输出，不是模型实验。

报告同时显示逐句等权的**样例平均 CER**、按参考字符数加权的**语料级 CER**及两版差值。可以筛选 CER 升高、降低、不变的句子，也可以搜索样例 ID 或文本，再展开两版的字符编辑记录。

导入自己的数据时，用 UTF-8 CSV 保存以下四列。一行就是同一个样例的配对结果，不按文件顺序猜测对应关系：

```csv
sample_id,reference,baseline,candidate
sample-001,把客厅空调打开,把客厅空调打开,把客厅空调关闭
sample-002,请把数量改成3份,请把数量改成3,请把数量改成3份
```

```sh
python scripts/compare.py --input your-pairs.csv --output exports/my-comparison --baseline-name v1 --candidate-name v2
```

打开输出目录中的 `index.html`。目录还包含原始 `input.csv`、带编辑证据和输入 SHA-256 的 `comparison.json`，以及逐句分数 `comparison.csv`。只使用本地 Python 标准库，不上传输入、不调用模型。线上页面用于浏览示例；导入在本地通过上述命令完成。

导入与解释约定：

- `sample_id` 不能为空或重复；CSV 支持 UTF-8 BOM、带引号的逗号和换行。缺列或缺单元格会报错。
- 空输出是有效结果；归一化后为空的参考文本标为“未计入”，不参与两版均值。每个文本字段最多 1000 个归一化字符，较长录音请先分段。
- 差值是“新版 − 旧版”，以百分点显示；负值表示 CER 降低。CER 可以超过 100%，同分也可能错在不同位置。
- 用相同测试集、参考标注和可比的推理设置。工具只比较提供的文本，不能从 CER 判断关键语义、任务成功率或统计显著性。
- 输出目录必须尚不存在。汇总 CSV 对疑似公式的文本加前置单引号；JSON 与原始输入保留原文，原始 CSV 打开时请按文本导入。

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

## 样例数据

`data/fixtures` 是六条入门样例，`data/generated_verify3` 是较大的合成集。它们都不包含真实用户录音。
