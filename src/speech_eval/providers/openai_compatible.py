"""OpenAI-compatible chat-completions provider using the standard library.

Network access is disabled by default.  Callers must explicitly set
``allow_network=True``; merely constructing the adapter or importing this
module never performs I/O and does not require an API key.
"""

from __future__ import annotations

import json
import os
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..diagnosis import DiagnosticContext, DiagnosticResult
from .base import (
    InvalidJSONError,
    MissingFieldError,
    InvalidStructureError,
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderTransportError,
    validate_diagnostic_response,
)

Transport = Callable[[Request, float], bytes | str | dict[str, Any]]

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PROMPT_MANIFEST = _PROJECT_ROOT / "config" / "prompts" / "prompt-manifest.json"
_TEMPLATE_VARIABLE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_CANONICAL_OUTPUT_FIELDS = {"质量诊断", "影响评估", "优化建议", "最终结论"}


@dataclass(frozen=True, slots=True)
class _PromptContract:
    system_prompt: str
    user_prompt: str
    output_schema: dict[str, Any]
    prompt_version: str
    required_variables: tuple[str, ...]


class OpenAICompatibleDiagnosticProvider:
    """Minimal ``POST /chat/completions`` diagnostic adapter."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:8000/v1",
        api_key: str | None = None,
        timeout: float = 30.0,
        allow_network: bool = False,
        temperature: float = 0.0,
        prompt_version: str | None = None,
        prompt_manifest_path: str | Path | None = None,
        prompt_root: str | Path | None = None,
        transport: Transport | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if prompt_version is not None and not prompt_version.strip():
            raise ValueError("prompt_version must be non-empty when provided")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.allow_network = allow_network
        self.temperature = temperature
        self._prompt_version_override = prompt_version
        self.prompt_root = Path(prompt_root).resolve() if prompt_root else _PROJECT_ROOT
        configured_manifest = prompt_manifest_path or os.environ.get(
            "SPEECH_EVAL_PROMPT_MANIFEST"
        )
        if configured_manifest:
            manifest_path = Path(configured_manifest)
            if not manifest_path.is_absolute():
                manifest_path = self.prompt_root / manifest_path
            self.prompt_manifest_path = manifest_path.resolve()
        else:
            self.prompt_manifest_path = _DEFAULT_PROMPT_MANIFEST
        self._prompt_contract: _PromptContract | None = None
        self.transport = transport

    @property
    def prompt_version(self) -> str:
        return self._prompt_version_override or self._load_prompt_contract().prompt_version

    def _read_contract_text(self, path: Path, field_name: str) -> str:
        try:
            return path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ProviderConfigurationError(
                f"unable to read {field_name}: {path}", cause=exc
            ) from exc

    def _resolve_contract_path(self, value: Any, field_name: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigurationError(
                f"prompt manifest field {field_name} must be a non-empty path"
            )
        path = Path(value)
        return path if path.is_absolute() else self.prompt_root / path

    def _load_prompt_contract(self) -> _PromptContract:
        if self._prompt_contract is not None:
            return self._prompt_contract

        raw_manifest = self._read_contract_text(
            self.prompt_manifest_path, "prompt manifest"
        )
        try:
            manifest = json.loads(raw_manifest)
        except json.JSONDecodeError as exc:
            raise ProviderConfigurationError(
                f"prompt manifest is not valid JSON: {self.prompt_manifest_path}",
                cause=exc,
            ) from exc
        if not isinstance(manifest, dict):
            raise ProviderConfigurationError("prompt manifest root must be an object")

        system_entry = manifest.get("system_prompt")
        user_entry = manifest.get("user_prompt")
        if not isinstance(system_entry, dict) or not isinstance(user_entry, dict):
            raise ProviderConfigurationError(
                "prompt manifest must define system_prompt and user_prompt objects"
            )
        required_variables = user_entry.get("required_variables")
        if (
            not isinstance(required_variables, list)
            or not required_variables
            or any(not isinstance(item, str) or not item for item in required_variables)
            or len(required_variables) != len(set(required_variables))
        ):
            raise ProviderConfigurationError(
                "user_prompt.required_variables must be a non-empty unique string array"
            )

        system_path = self._resolve_contract_path(
            system_entry.get("path"), "system_prompt.path"
        )
        user_path = self._resolve_contract_path(
            user_entry.get("path"), "user_prompt.path"
        )
        schema_path = self._resolve_contract_path(
            manifest.get("output_schema"), "output_schema"
        )
        system_prompt = self._read_contract_text(system_path, "system prompt")
        user_prompt = self._read_contract_text(user_path, "user prompt")
        raw_schema = self._read_contract_text(schema_path, "output schema")
        try:
            output_schema = json.loads(raw_schema)
        except json.JSONDecodeError as exc:
            raise ProviderConfigurationError(
                f"output schema is not valid JSON: {schema_path}", cause=exc
            ) from exc
        if not isinstance(output_schema, dict):
            raise ProviderConfigurationError("output schema root must be an object")

        placeholders = set(_TEMPLATE_VARIABLE.findall(user_prompt))
        required_set = set(required_variables)
        if placeholders != required_set:
            missing = sorted(required_set - placeholders)
            undeclared = sorted(placeholders - required_set)
            raise ProviderConfigurationError(
                "user prompt variables do not match the manifest; "
                f"missing={missing}, undeclared={undeclared}"
            )
        schema_required = output_schema.get("required")
        if (
            not isinstance(schema_required, list)
            or set(schema_required) != _CANONICAL_OUTPUT_FIELDS
            or output_schema.get("additionalProperties") is not False
        ):
            raise ProviderConfigurationError(
                "output schema must require exactly the four canonical analysis fields"
            )
        manifest_prompt_version = manifest.get("prompt_version")
        if not isinstance(manifest_prompt_version, str) or not manifest_prompt_version.strip():
            raise ProviderConfigurationError(
                "prompt manifest prompt_version must be a non-empty string"
            )

        self._prompt_contract = _PromptContract(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=output_schema,
            prompt_version=manifest_prompt_version,
            required_variables=tuple(required_variables),
        )
        return self._prompt_contract

    @staticmethod
    def _json_template_value(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _render_user_prompt(
        self,
        contract: _PromptContract,
        context: DiagnosticContext,
    ) -> str:
        sample = context.sample
        capability_chain: Any = "-"
        if isinstance(sample.input_data, dict):
            capability_chain = sample.input_data.get("capability_chain", "-")
        threshold_config = {
            **dict(context.thresholds),
            "threshold_version": context.threshold_version,
            "threshold_approval_status": context.threshold_approval_status,
            "threshold_effective": context.threshold_effective,
        }
        values = {
            "sample_id": self._json_template_value(sample.sample_id),
            "scenario_json": self._json_template_value(
                {
                    "scene_type": sample.scene_type,
                    "subscene_type": sample.subscene_type,
                }
            ),
            "task_types_json": self._json_template_value(list(sample.task_types)),
            "capability_chain_json": self._json_template_value(capability_chain),
            "input_data_json": self._json_template_value(sample.input_data),
            "audio_info_json": self._json_template_value(sample.audio_info),
            "reference_annotation_json": self._json_template_value(
                sample.reference_annotation
            ),
            "system_output_json": self._json_template_value(sample.system_output),
            "metrics_json": self._json_template_value(context.metrics),
            "threshold_config_json": self._json_template_value(threshold_config),
            "evidence_catalog_json": self._json_template_value(context.evidence),
        }
        missing_values = sorted(set(contract.required_variables) - values.keys())
        if missing_values:
            raise ProviderConfigurationError(
                f"provider cannot supply prompt variables: {', '.join(missing_values)}"
            )
        return _TEMPLATE_VARIABLE.sub(
            lambda match: values[match.group(1)], contract.user_prompt
        )

    def _request_payload(self, context: DiagnosticContext) -> dict[str, Any]:
        if not context.evidence:
            raise ProviderConfigurationError(
                "evidence_catalog must contain at least one evidence item"
            )
        contract = self._load_prompt_contract()
        return {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "speech_eval_analysis_output",
                    "strict": True,
                    "schema": contract.output_schema,
                },
            },
            "messages": [
                {"role": "system", "content": contract.system_prompt},
                {
                    "role": "user",
                    "content": self._render_user_prompt(contract, context),
                },
            ],
        }

    def _perform_request(self, request: Request) -> bytes | str | dict[str, Any]:
        if self.transport is not None:
            return self.transport(request, self.timeout)
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            return response.read()

    def diagnose(self, context: DiagnosticContext) -> DiagnosticResult:
        if not self.allow_network:
            raise ProviderConfigurationError(
                "network calls are disabled; set allow_network=True explicitly"
            )
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(self._request_payload(context), ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            raw_response = self._perform_request(request)
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError("OpenAI-compatible request timed out", cause=exc) from exc
        except HTTPError as exc:
            raise ProviderTransportError(
                f"OpenAI-compatible HTTP error: {exc.code}", cause=exc
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeoutError(
                    "OpenAI-compatible request timed out", cause=exc
                ) from exc
            raise ProviderTransportError(
                f"OpenAI-compatible transport error: {exc.reason}", cause=exc
            ) from exc
        except OSError as exc:
            raise ProviderTransportError(
                f"OpenAI-compatible transport error: {exc}", cause=exc
            ) from exc

        if isinstance(raw_response, dict):
            envelope = raw_response
        else:
            if isinstance(raw_response, bytes):
                raw_response = raw_response.decode("utf-8")
            try:
                envelope = json.loads(raw_response)
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
                raise InvalidJSONError(
                    "OpenAI-compatible response envelope is not valid JSON",
                    cause=exc,
                ) from exc
        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise MissingFieldError(
                "OpenAI-compatible response is missing choices[0].message.content",
                fields=["choices[0].message.content"],
            ) from exc
        result = validate_diagnostic_response(
            content,
            default_prompt_version=self.prompt_version,
            evidence_catalog=context.evidence,
        )
        if context.evidence:
            expected = {
                str(item.get("evidence_id")): item
                for item in context.evidence
                if isinstance(item, dict) and item.get("evidence_id")
            }
            actual = {
                str(item.get("evidence_id")): item
                for item in result.evidence
                if isinstance(item, dict) and item.get("evidence_id")
            }
            if actual != expected:
                raise InvalidStructureError(
                    "provider evidence must exactly preserve the supplied evidence catalog"
                )
        return result


OpenAICompatibleProvider = OpenAICompatibleDiagnosticProvider

