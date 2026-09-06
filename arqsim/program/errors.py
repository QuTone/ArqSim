"""Structured errors for the workload synthesis frontend."""

from __future__ import annotations

from typing import Any, Mapping, Optional


class SynthesisError(RuntimeError):
    """Base class for errors that can be surfaced through the CLI or an API."""

    code = "synthesis_error"

    def __init__(
        self,
        message: str,
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.details:
            payload["error"]["details"] = self.details
        return payload


class InputValidationError(SynthesisError):
    code = "invalid_synthesis_input"


class WorkloadParseError(SynthesisError):
    code = "invalid_workload"


class UnsupportedBackendError(SynthesisError):
    code = "unsupported_synthesis_backend"


class BackendUnavailableError(SynthesisError):
    code = "synthesis_backend_unavailable"


class UnsupportedSynthesisPassError(SynthesisError):
    code = "unsupported_synthesis_pass"


class SynthesisExecutionError(SynthesisError):
    code = "synthesis_execution_failed"


class ArtifactIntegrityError(SynthesisError):
    code = "synthesis_artifact_integrity_error"


class ExportConflictError(SynthesisError):
    code = "synthesis_export_conflict"
