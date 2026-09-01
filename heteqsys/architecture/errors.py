"""Structured errors for architecture contracts and resolved specifications."""

from __future__ import annotations

from typing import Any, Mapping


class ArchitectureError(ValueError):
    """Base error carrying a stable code and machine-readable details."""

    code = "architecture_error"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": self.details,
        }


class ArchitectureValidationError(ArchitectureError):
    code = "invalid_architecture"


class UnsupportedArchitectureError(ArchitectureError):
    code = "unsupported_architecture"
