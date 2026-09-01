"""Compatibility names for the SQLite repository."""

from .repository import EvaluationRepository

SQLiteStore = EvaluationRepository
Repository = EvaluationRepository

__all__ = ["EvaluationRepository", "SQLiteStore", "Repository"]
