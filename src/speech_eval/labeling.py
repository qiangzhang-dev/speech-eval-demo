"""Conservative scene/task suggestions for manifests with missing labels.

The classifier only uses explicit strings already present in the sample row.
It never infers audio conditions, user intent, or business facts. A caller can
therefore distinguish an automatic suggestion from a human-provided label and
keep the suggestion available for later confirmation or revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


SCENE_TASKS: dict[str, list[str]] = {
    "短语音/录音转写": ["语音识别"],
    "会议/办公语音翻译": ["语音识别", "机器翻译"],
    "车载/语音助手交互": ["语音识别", "语义理解", "对话生成"],
}

CHAIN_SCENES = {
    "c01": "短语音/录音转写",
    "c02": "会议/办公语音翻译",
    "c03": "车载/语音助手交互",
}

SCENE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "会议/办公语音翻译",
        ("翻译", "语翻", "同传", "译文", "translation", "translate", "meeting"),
    ),
    (
        "车载/语音助手交互",
        (
            "车载", "座舱", "语音助手", "设备控制", "多轮", "导航", "空调",
            "意图", "槽位", "assistant", "intent", "slot",
        ),
    ),
    (
        "短语音/录音转写",
        ("转写", "识别", "录音", "口述", "噪声", "asr", "transcript"),
    ),
)


@dataclass(frozen=True, slots=True)
class LabelSuggestion:
    scene_type: str
    task_types: list[str]
    source: str
    confidence: str
    matched_signals: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_type": self.scene_type,
            "task_types": list(self.task_types),
            "source": self.source,
            "confidence": self.confidence,
            "matched_signals": list(self.matched_signals),
            "requires_human_confirmation": True,
        }


def _flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _flatten_strings(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _flatten_strings(child)


def suggest_labels(row: dict[str, Any]) -> LabelSuggestion | None:
    """Return a traceable suggestion, or ``None`` when evidence is ambiguous."""

    explicit_chain = next(
        (
            str(row[key]).strip().casefold()
            for key in ("capability_chain", "能力链路")
            if row.get(key) not in (None, "", "-")
        ),
        "",
    )
    if explicit_chain in CHAIN_SCENES:
        scene = CHAIN_SCENES[explicit_chain]
        return LabelSuggestion(
            scene_type=scene,
            task_types=list(SCENE_TASKS[scene]),
            source="explicit_capability_chain",
            confidence="high",
            matched_signals=[explicit_chain.upper()],
        )

    searchable_keys = (
        "subscene_type", "subscene", "子场景", "子场景类型", "input_data",
        "input", "输入数据", "reference_annotation", "reference_text",
        "参考文本/标注", "system_output", "系统输出", "metadata",
    )
    searchable = "\n".join(
        text.casefold()
        for key in searchable_keys
        if key in row
        for text in _flatten_strings(row[key])
    )
    matches: list[tuple[str, list[str]]] = []
    for scene, keywords in SCENE_KEYWORDS:
        hit = [keyword for keyword in keywords if keyword.casefold() in searchable]
        if hit:
            matches.append((scene, hit))
    if len(matches) != 1:
        return None
    scene, signals = matches[0]
    return LabelSuggestion(
        scene_type=scene,
        task_types=list(SCENE_TASKS[scene]),
        source="explicit_content_keyword_rules",
        confidence="medium",
        matched_signals=signals,
    )
