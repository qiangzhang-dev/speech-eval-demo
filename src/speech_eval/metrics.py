"""Deterministic character error rate and edit evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import MISSING
from .normalization import NORMALIZATION_VERSION, normalize_text

METRIC_VERSION = "cer-v1"


@dataclass(slots=True)
class CERResult:
    value: float | None
    substitutions: int
    deletions: int
    insertions: int
    reference_length: int
    normalized_reference: str
    normalized_hypothesis: str
    operations: list[dict[str, Any]]
    metric_version: str = METRIC_VERSION
    normalization_version: str = NORMALIZATION_VERSION
    applicable: bool = True
    reason: str | None = None

    @property
    def interpretation(self) -> str:
        if self.value is None:
            return "不适用"
        if self.value == 0:
            return "完全一致"
        return "存在字符级差异"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": "CER",
            "value": self.value,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "reference_length": self.reference_length,
            "normalized_reference": self.normalized_reference,
            "normalized_hypothesis": self.normalized_hypothesis,
            "operations": self.operations,
            "metric_version": self.metric_version,
            "normalization_version": self.normalization_version,
            "applicable": self.applicable,
            "reason": self.reason,
            "interpretation": self.interpretation,
        }


def compute_cer(reference: str | None, hypothesis: str | None) -> CERResult:
    """Compute CER using Levenshtein dynamic programming.

    Tie-breaking is deterministic: diagonal, deletion, insertion. Operations
    are returned as evidence-friendly character edits.
    """

    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    if not ref:
        return CERResult(
            value=None,
            substitutions=0,
            deletions=0,
            insertions=len(hyp),
            reference_length=0,
            normalized_reference=ref,
            normalized_hypothesis=hyp,
            operations=[],
            applicable=False,
            reason="参考文本为空，CER不适用",
        )
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    back: list[list[str | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i
        back[i][0] = "D"
    for j in range(1, m + 1):
        dp[0][j] = j
        back[0][j] = "I"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            same = ref[i - 1] == hyp[j - 1]
            candidates = [
                (dp[i - 1][j - 1] + (0 if same else 1), "M" if same else "S"),
                (dp[i - 1][j] + 1, "D"),
                (dp[i][j - 1] + 1, "I"),
            ]
            dp[i][j], back[i][j] = min(candidates, key=lambda item: (item[0], {"M": 0, "S": 0, "D": 1, "I": 2}[item[1]]))
    ops: list[dict[str, Any]] = []
    i, j = n, m
    while i or j:
        op = back[i][j]
        if op in {"M", "S"}:
            if op == "S":
                ops.append({"op": "substitution", "reference_index": i - 1, "reference": ref[i - 1], "hypothesis_index": j - 1, "hypothesis": hyp[j - 1]})
            i -= 1
            j -= 1
        elif op == "D":
            # Missing evidence sides use the frozen ``-`` marker rather than
            # null/empty strings so every export preserves the same meaning.
            ops.append({"op": "deletion", "reference_index": i - 1, "reference": ref[i - 1], "hypothesis_index": MISSING, "hypothesis": MISSING})
            i -= 1
        elif op == "I":
            ops.append({"op": "insertion", "reference_index": i, "reference": MISSING, "hypothesis_index": j - 1, "hypothesis": hyp[j - 1]})
            j -= 1
        else:  # defensive guard; should be unreachable
            raise RuntimeError("invalid edit backtrace")
    ops.reverse()
    substitutions = sum(op["op"] == "substitution" for op in ops)
    deletions = sum(op["op"] == "deletion" for op in ops)
    insertions = sum(op["op"] == "insertion" for op in ops)
    return CERResult(
        value=(substitutions + deletions + insertions) / n,
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        reference_length=n,
        normalized_reference=ref,
        normalized_hypothesis=hyp,
        operations=ops,
    )


cer = compute_cer
