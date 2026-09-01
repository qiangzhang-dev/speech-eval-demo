"""Offline-first speech evaluation demo core package.

The package deliberately uses only the Python standard library so that the
sample pipeline can be reproduced without network access or an API key.
"""

from .batch import BatchProcessor, BatchRunSummary
from .diagnosis import DiagnosticEngine, DiagnosticResult
from .manifest import ManifestError, load_manifest, validate_manifest
from .metrics import CERResult, cer, compute_cer
from .models import EvaluationResult, Sample, ValidationIssue
from .normalization import normalize_text
from .repository import EvaluationRepository

__all__ = [
    "BatchProcessor",
    "BatchRunSummary",
    "CERResult",
    "DiagnosticEngine",
    "DiagnosticResult",
    "EvaluationRepository",
    "EvaluationResult",
    "ManifestError",
    "Sample",
    "ValidationIssue",
    "cer",
    "compute_cer",
    "load_manifest",
    "normalize_text",
    "validate_manifest",
]

__version__ = "0.1.0"
