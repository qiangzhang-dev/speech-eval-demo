"""Shared implementation of the versioned CER normalization contract."""

from __future__ import annotations

import unicodedata

NORMALIZATION_VERSION = "cer-normalize-v1"


def _allowed(character: str) -> bool:
    category = unicodedata.category(character)
    codepoint = ord(character)
    is_han = (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )
    is_latin = "LATIN" in unicodedata.name(character, "") and category.startswith("L")
    return is_han or is_latin or category == "Nd"


def normalize_text(value: str | None, *, version: str = NORMALIZATION_VERSION) -> str:
    """Apply NFKC/casefold and keep only Han, Latin, and decimal digits."""

    if version != NORMALIZATION_VERSION:
        raise ValueError(f"unsupported normalization version: {version}")
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if _allowed(character))


__all__ = ["NORMALIZATION_VERSION", "normalize_text"]
