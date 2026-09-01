"""Compatibility entry point for versioned CER text normalization."""

from __future__ import annotations

from ._normalization import NORMALIZATION_VERSION, normalize_text

__all__ = ["NORMALIZATION_VERSION", "normalize_text"]
