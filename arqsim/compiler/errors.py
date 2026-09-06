"""Structured errors for logical mapping, routing, and plan emission."""

from __future__ import annotations

from typing import Any, Mapping


class LogicalCompilerError(RuntimeError):
    code = "logical_compiler_error"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


class LogicalCompilerValidationError(LogicalCompilerError):
    code = "invalid_logical_compiler_input"


class UnsupportedLogicalBackendError(LogicalCompilerError):
    code = "unsupported_logical_backend"


class LogicalMappingError(LogicalCompilerError):
    code = "logical_mapping_failed"


class LogicalRoutingError(LogicalCompilerError):
    code = "logical_routing_failed"


class CompilerCacheError(LogicalCompilerError):
    code = "compiler_cache_error"
