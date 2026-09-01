"""Immutable, content-addressed synthesis artifact."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from heteqsys.program.circuit import (
    WORKLOAD_SCHEMA_VERSION,
    normalize_json_value,
    sha256_file,
)
from heteqsys.program.errors import (
    ArtifactIntegrityError,
    ExportConflictError,
)


ARTIFACT_SCHEMA_VERSION = "heteqsys.synthesis-artifact.v1"


@dataclass(frozen=True)
class SynthesisArtifact:
    """A verified cache entry containing a circuit and canonical workload."""

    cache_key: str
    representation: str
    source: Mapping[str, Any]
    backend: Mapping[str, Any]
    parameters: Mapping[str, Any]
    stats: Mapping[str, Any]
    artifact_sha256: str
    workload_sha256: str
    created_at_utc: str
    artifact_dir: Path
    cache_hit: bool = False

    @property
    def circuit_path(self) -> Path:
        return self.artifact_dir / "circuit.qasm"

    @property
    def workload_path(self) -> Path:
        return self.artifact_dir / "workload.json"

    @property
    def manifest_path(self) -> Path:
        return self.artifact_dir / "artifact.json"

    def manifest_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "workload_schema_version": WORKLOAD_SCHEMA_VERSION,
            "cache_key": self.cache_key,
            "representation": self.representation,
            "source": normalize_json_value(self.source),
            "backend": normalize_json_value(self.backend),
            "parameters": normalize_json_value(self.parameters),
            "stats": normalize_json_value(self.stats),
            "files": {
                "circuit": "circuit.qasm",
                "workload": "workload.json",
            },
            "artifact_sha256": self.artifact_sha256,
            "workload_sha256": self.workload_sha256,
            "created_at_utc": self.created_at_utc,
        }

    def summary_dict(self) -> dict[str, Any]:
        result = self.manifest_dict()
        result["cache_hit"] = self.cache_hit
        result["paths"] = {
            "artifact_dir": str(self.artifact_dir),
            "circuit": str(self.circuit_path),
            "workload": str(self.workload_path),
            "manifest": str(self.manifest_path),
        }
        return result

    def export_circuit(
        self,
        destination: Path | str,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Atomically materialize the canonical circuit at a user-owned path."""

        return self._export_file(
            source=self.circuit_path,
            expected_hash=self.artifact_sha256,
            destination=destination,
            overwrite=overwrite,
            label="circuit",
        )

    def export_workload(
        self,
        destination: Path | str,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Atomically materialize the canonical FT-circuit JSON."""

        return self._export_file(
            source=self.workload_path,
            expected_hash=self.workload_sha256,
            destination=destination,
            overwrite=overwrite,
            label="workload",
        )

    @staticmethod
    def _export_file(
        *,
        source: Path,
        expected_hash: str,
        destination: Path | str,
        overwrite: bool,
        label: str,
    ) -> Path:
        """Copy one verified artifact payload without exposing partial output."""

        destination_path = Path(destination).expanduser().absolute()
        if sha256_file(source) != expected_hash:
            raise ArtifactIntegrityError(
                f"The canonical {label} changed after its artifact was loaded",
                details={"path": str(source)},
            )

        destination_exists = os.path.lexists(destination_path)
        if destination_exists and destination_path.is_file():
            if sha256_file(destination_path) == expected_hash:
                return destination_path
            if not overwrite:
                raise ExportConflictError(
                    "The export destination already contains different content",
                    details={"path": str(destination_path)},
                )
        elif destination_exists:
            raise ExportConflictError(
                "The export destination exists and is not a regular file",
                details={"path": str(destination_path)},
            )

        try:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination_path.name}.",
                suffix=".tmp",
                dir=str(destination_path.parent),
            )
            os.close(file_descriptor)
        except OSError as exc:
            raise ExportConflictError(
                "The export destination could not be prepared",
                details={"path": str(destination_path), "reason": str(exc)},
            ) from exc

        temporary_path = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary_path)
            if sha256_file(temporary_path) != expected_hash:
                raise ArtifactIntegrityError(
                    f"The exported {label} does not match the canonical artifact",
                    details={"path": str(temporary_path)},
                )
            if overwrite:
                os.replace(temporary_path, destination_path)
            else:
                try:
                    os.link(temporary_path, destination_path)
                except FileExistsError:
                    if (
                        destination_path.is_file()
                        and sha256_file(destination_path) == expected_hash
                    ):
                        return destination_path
                    raise ExportConflictError(
                        "The export destination was created by another process",
                        details={"path": str(destination_path)},
                    )
            return destination_path
        except (ArtifactIntegrityError, ExportConflictError):
            raise
        except OSError as exc:
            raise ExportConflictError(
                f"The {label} could not be exported",
                details={"path": str(destination_path), "reason": str(exc)},
            ) from exc
        finally:
            temporary_path.unlink(missing_ok=True)

    @classmethod
    def from_manifest(
        cls,
        data: Mapping[str, Any],
        *,
        artifact_dir: Path,
        cache_hit: bool,
    ) -> "SynthesisArtifact":
        schema = data.get("schema_version")
        if schema != ARTIFACT_SCHEMA_VERSION:
            raise WorkloadParseError(
                f"Unsupported synthesis artifact schema: {schema}",
                details={
                    "schema_version": schema,
                    "supported": ARTIFACT_SCHEMA_VERSION,
                },
            )
        workload_schema = data.get("workload_schema_version")
        if workload_schema != WORKLOAD_SCHEMA_VERSION:
            raise WorkloadParseError(
                f"Unsupported artifact FT-circuit schema: {workload_schema}",
                details={
                    "workload_schema_version": workload_schema,
                    "supported": WORKLOAD_SCHEMA_VERSION,
                },
            )
        return cls(
            cache_key=str(data["cache_key"]),
            representation=str(data["representation"]),
            source=data["source"],
            backend=data["backend"],
            parameters=data["parameters"],
            stats=data["stats"],
            artifact_sha256=str(data["artifact_sha256"]),
            workload_sha256=str(data["workload_sha256"]),
            created_at_utc=str(data["created_at_utc"]),
            artifact_dir=artifact_dir,
            cache_hit=cache_hit,
        )
