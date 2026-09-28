# Whisper tiny.en: beam 1 与 beam 5

固定 LibriSpeech test-clean 的 40 条真实录音，比较相同模型权重下两种解码设置。每位说话人选一条，选择规则在推理前固定：对 `nate-asr-v1:<utterance_id>` 计算 SHA-256，取该说话人哈希值最小的样本；按说话人编号排列。没有根据识别结果挑选输入。

实际结果：3 条 CER 降低、37 条不变、0 条升高。语料级 CER 为 2.82% → 2.48%；这是固定小样本的描述统计。[阅读实验记录](https://qiangzhang-dev.github.io/notes/whisper-beam/) · [逐句对照](https://qiangzhang-dev.github.io/notes/whisper-beam/report/)。

## 复现

在仓库根目录使用 Python 3.12 创建独立环境，再安装锁定依赖：

```sh
python -m venv .venv-asr
# macOS / Linux
. .venv-asr/bin/activate
# Windows PowerShell 则运行 .venv-asr/Scripts/Activate.ps1
python -m pip install -r experiments/librispeech-beam/requirements-lock.txt
python scripts/librispeech_experiment.py --download --output exports/librispeech-run
```

首次下载约 347 MB 的数据集压缩包及约 76 MB 的模型。缓存默认保存在 `exports/asr-cache`；后续使用相同缓存时可去掉 `--download`。结果目录必须尚不存在。数据与模型下载完成后，推理阶段禁止网络连接。不同 CPU 或依赖平台的数值差异可能影响临界解码结果。

`protocol.json` 固定数据校验和、模型提交版本、量化类型、线程数和显式解码参数。`results/manifest.json` 记录样本 ID、说话人、参考标注及音频校验和；`raw-results.jsonl` 记录原始输出、分段、耗时与平均 log probability。`pairs.csv` 可以直接交给通用对比工具。

## 计算口径

CER 沿用仓库的 `cer-v1 / cer-normalize-v1`。英文 WER 使用另行声明的简单规则：NFKC、casefold，按 `[a-z0-9]+(?:'[a-z0-9]+)*` 分词；不做数字展开或缩写展开。它不是 LibriSpeech 官方评分流程，不能直接与排行榜的 WER 比较。

所有结果均为描述统计。40 条录音各来自不同说话人，但不是完整测试集，也不是中文对话、噪声或语音交互任务。CER 降低不等于语义和任务效果改善。单次运行的耗时只作记录，不作为吞吐量或实时性能评测。

## 数据与模型来源

- LibriSpeech，Vassil Panayotov、Guoguo Chen、Daniel Povey、Sanjeev Khudanpur，2015：[OpenSLR 12](https://www.openslr.org/12/)，[论文](https://www.danielpovey.com/files/2015_icassp_librispeech.pdf)。数据采用 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。本仓库发布所选参考文本、样本清单和派生识别结果，未修改原始录音，也不重新托管音频。
- [OpenAI Whisper](https://github.com/openai/whisper)；实际加载 [Systran/faster-whisper-tiny.en](https://huggingface.co/Systran/faster-whisper-tiny.en) 的 CTranslate2 转换权重，固定提交见协议。
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)，运行版本与完整依赖见锁文件。模型、工具与数据各自遵循原许可。

本实验是个人公开项目，与雇主业务数据或内部模型无关。

文章源文件是 `NOTE.md`。如需重新渲染静态文章页，另行安装 `markdown2==2.5.4`，再运行 `python scripts/render_experiment_note.py`；它不是模型推理依赖。
