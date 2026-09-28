# CER 为 0，语音识别就一定正确吗？一次 Whisper 小实验

一条录音的参考文本是：

> YOU ARE ACUTE

Whisper 的输出却是：

> You are a cute.

单词的分界变了，CER（字符错误率）却是 0。原因很直接：这次采用的归一化会去掉空格，`acute` 和 `a cute` 都变成同一串字符。

这条不是手工构造的反例。它来自 LibriSpeech test-clean，样本 ID 是 `121-127105-0014`。这次固定 Whisper tiny.en 的权重，在 40 条真实录音上比较 beam size 1 和 5，两个设置都给出了这个输出。

整体上，**40 条里，3 条的 CER 降低、37 条不变，没有 CER 升高的样例。** 语料级 CER 从 2.82% 降到 2.48%。但两版原始文本实际有 7 条不同。下面把这几个数字和具体句子放在一起看。

## 先固定输入，再跑模型

数据来自 LibriSpeech test-clean。对每位说话人的 utterance ID 加上固定前缀后计算 SHA-256，选哈希值最小的一条，共 40 位说话人、40 条录音，总长约 5 分 24 秒。选择规则不看识别结果；全部样本 ID、参考文本和音频校验和都已公开。

两次使用相同的 tiny.en 转换权重、CPU int8、4 个线程，temperature 固定为 0，关闭 VAD、跨片段文本条件和温度回退。只改变 beam size。具体参数与模型提交版本见 [实验协议](https://qiangzhang-dev.github.io/notes/whisper-beam/protocol.json)。本次全部使用公开数据。

## 总分变化不大，先看改对了什么

| 指标 | beam 1 | beam 5 |
| --- | ---: | ---: |
| 样例平均 CER | 2.91% | 2.47% |
| 语料级 CER | 2.82% | 2.48% |
| 字符编辑总数 | 106 | 93 |
| 语料级 WER（本文分词规则） | 7.67% | 6.76% |

样例平均 CER 对每句话等权；语料级 CER 用全部字符编辑次数除以全部参考字符数。这次有 3,754 个参考字符、873 个参考词。表里的差值是描述统计，没有做显著性检验。

最明显的一条是 `1284-1180-0013`：

| 来源 | 文本 |
| --- | --- |
| 参考 | AND YOU MUST BE OJO THE UNLUCKY SHE ADDED |
| beam 1 | And you must be Ojodia and Lucky, she added. |
| beam 5 | And you must be Ojo the unlucky, she added. |

这条的 CER 从 15.15% 降到了 0。更宽的搜索在这个样本上找到了与参考一致的输出；仅凭结果，不能判断差异具体来自哪个候选路径，也不能推出专名识别整体得到改善。

另一个样本 `5639-40744-0032` 中，`in agreeable companion` 变成了与参考一致的 `an agreeable companion`。同一句里，参考的 `disgust me` 仍被识别成 `discuss me`。分数下降了，错误也确实还在。

## 文本变了，CER 为什么没变？

这里使用的 CER 会去掉标点和空格。7 条文本不同的样本里，4 条的 CER 不变：其中 3 条主要是标点位置变化，另 1 条包括 `high readgrass` 变成 `high-read grass`。去掉空格和连字符后，两者的字符序列相同。

这个指标本来就不保留空格和标点信息。它适合看字符层面的错误，却不能告诉我们停顿是否合理、单词分界是否正确，更不能代替语义或任务成功率。

英文 WER 因而单独计算。本文的规则是 NFKC、casefold，再按保留词内撇号的英文词与数字分词；不展开数字和缩写。**这不是 LibriSpeech 官方评分流程，不能拿这个 WER 与排行榜直接比较。** 完整口径与原始输出见 [仓库记录](https://github.com/qiangzhang-dev/speech-eval-demo/tree/main/experiments/librispeech-beam/results)。

## 这次能说明到哪里

在这 40 条固定录音和这一套运行条件下，beam 5 减少了 13 次字符编辑，改善集中在 3 条录音。没有观察到 CER 回退，是这次样本的结果，不是“beam 越大越好”的保证。

这只是英语有声书朗读的一个小切片，不能代表完整 test-clean，更不能外推到中文对话、噪声环境或语音交互。没有在这里比较实时性能；单次耗时已留在原始记录中。

下一次如果扩大实验，我会先增加样本覆盖，再看改善和退步分别落在哪些句子上。只把 beam 从 1 改成 5、看到平均分降低，还不够作出部署选择。

## 数据来源与复现

[网站原文](https://qiangzhang-dev.github.io/notes/whisper-beam/) · [40 条逐句对比](https://qiangzhang-dev.github.io/notes/whisper-beam/report/) · [复现脚本](https://github.com/qiangzhang-dev/speech-eval-demo/tree/main/experiments/librispeech-beam)


- [LibriSpeech / OpenSLR 12](https://www.openslr.org/12/)：Vassil Panayotov、Guoguo Chen、Daniel Povey、Sanjeev Khudanpur，2015；[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。所选参考文本和派生识别结果保留此来源说明；未修改或重新托管原始音频。
- 模型为 [Systran 转换的 Whisper tiny.en](https://huggingface.co/Systran/faster-whisper-tiny.en)，运行工具为 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)。版本和参数已固定。
- [样本清单](https://qiangzhang-dev.github.io/notes/whisper-beam/manifest.json) · [原始输出](https://qiangzhang-dev.github.io/notes/whisper-beam/raw-results.jsonl) · [统计结果](https://qiangzhang-dev.github.io/notes/whisper-beam/summary.json) · [完整复现步骤](https://github.com/qiangzhang-dev/speech-eval-demo/tree/main/experiments/librispeech-beam#复现)。

