"""Public repository package facade.

The package form intentionally takes precedence over the early prototype
module so existing ``from speech_eval.repository import ...`` imports receive
the corrected implementation.
"""

from ..sqlite_repository import EvaluationRepository, SQLiteEvaluationRepository

__all__ = ["EvaluationRepository", "SQLiteEvaluationRepository"]
