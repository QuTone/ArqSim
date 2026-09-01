"""Content-addressed synthesis service and artifact cache."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from .interface import SynthesisBackend, SynthesisSpec
from .nwqec import NWQECSynthesizer
from .precomputed import PrecomputedCircuitLoader
from heteqsys.program.errors import (
    ArtifactIntegrityError,
    InputValidationError,
    SynthesisError,
    UnsupportedBackendError,
)
from heteqsys.program.circuit import (
    WORKLOAD_SCHEMA_VERSION,
    canonical_json,
    normalize_json_value,
    sha256_bytes,
    sha256_file,
)
from heteqsys.program import FTCircuit, load_ft_workload, workload_stats
from .artifact import ARTIFACT_SCHEMA_VERSION, SynthesisArtifact


DEFAULT_CACHE_DIR = Path.home() / ".cache" / "heteqsys" / "synthesis"


def default_synthesis_backends() -> dict[str, SynthesisBackend]:
    backends = (PrecomputedCircuitLoader(), NWQECSynthesizer())
    return {backend.name: backend for backend in backends}


SUPPORTED_SOURCE_TYPES = frozenset({"path", "benchmark", "upload"})


class Synthesizer:
    """Run synthesis passes and persist immutable, verified cache entries."""

    def __init__(
        self,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        *,
        backends: Optional[Mapping[str, SynthesisBackend]] = None,
    ) -> None:
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.backends = dict(backends or default_synthesis_backends())

    def synthesize(
        self,
        source: Path | str,
        spec: SynthesisSpec,
        *,
        source_type: str = "path",
        source_name: Optional[str] = None,
    ) -> SynthesisArtifact:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise InputValidationError(
                "The synthesis source does not exist or is not a file",
                details={"path": str(source_path)},
            )
        if source_type not in SUPPORTED_SOURCE_TYPES:
            raise InputValidationError(
                f"Unsupported synthesis source type: {source_type}",
                details={
                    "source_type": source_type,
                    "supported": sorted(SUPPORTED_SOURCE_TYPES),
                },
            )

        backend = self.backends.get(spec.backend)
        if backend is None:
            raise UnsupportedBackendError(
                f"Unsupported synthesis backend: {spec.backend}",
                details={
                    "backend": spec.backend,
                    "supported": sorted(self.backends),
                },
            )
        if backend.name != spec.backend:
            raise InputValidationError(
                "The backend registry key does not match the backend name",
                details={"key": spec.backend, "backend_name": backend.name},
            )

        backend_version = backend.version()
        effective_parameters = normalize_json_value(backend.effective_parameters(spec))
        source_hash = sha256_file(source_path)
        source_record = {
            "type": source_type,
            "name": source_name or source_path.name,
            "sha256": source_hash,
        }
        parameters = {
            "requested": spec.requested_parameters(),
            "effective": effective_parameters,
        }
        cache_payload = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "workload_schema_version": WORKLOAD_SCHEMA_VERSION,
            "source": source_record,
            "backend": {"name": backend.name, "version": backend_version},
            "representation": spec.representation,
            "parameters": parameters,
        }
        cache_key = sha256_bytes(canonical_json(cache_payload).encode("ascii"))
        artifact_dir = self.cache_dir / cache_key
        if artifact_dir.exists():
            return self._load_artifact(
                artifact_dir,
                expected_cache_key=cache_key,
                expected_source_hash=source_hash,
                cache_hit=True,
            )

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{cache_key}.", dir=str(self.cache_dir))
        )
        try:
            circuit_path = temp_dir / "circuit.qasm"
            backend_stats = backend.synthesize(
                source_path,
                circuit_path,
                spec,
                effective_parameters,
            )
            if not circuit_path.is_file():
                raise ArtifactIntegrityError(
                    "The synthesis backend did not produce a QASM artifact",
                    details={"backend": backend.name},
                )

            artifact_hash = sha256_file(circuit_path)
            workload = load_ft_workload(
                circuit_path,
                spec.representation,
                provenance={
                    "artifact_sha256": artifact_hash,
                    "cache_key": cache_key,
                    "backend": {"name": backend.name, "version": backend_version},
                },
            )
            workload_path = temp_dir / "workload.json"
            workload_path.write_text(workload.to_json(), encoding="utf-8")
            workload_hash = sha256_file(workload_path)

            stats = workload_stats(workload)
            if backend_stats:
                stats["backend"] = normalize_json_value(backend_stats)
            artifact = SynthesisArtifact(
                cache_key=cache_key,
                representation=spec.representation,
                source=source_record,
                backend={"name": backend.name, "version": backend_version},
                parameters=parameters,
                stats=stats,
                artifact_sha256=artifact_hash,
                workload_sha256=workload_hash,
                created_at_utc=datetime.now(timezone.utc).isoformat(),
                artifact_dir=artifact_dir,
                cache_hit=False,
            )
            (temp_dir / "artifact.json").write_text(
                json.dumps(artifact.manifest_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            try:
                os.replace(temp_dir, artifact_dir)
            except OSError:
                # A concurrent process may have populated the same immutable key.
                if not artifact_dir.is_dir():
                    raise
                shutil.rmtree(temp_dir)
                return self._load_artifact(
                    artifact_dir,
                    expected_cache_key=cache_key,
                    expected_source_hash=source_hash,
                    cache_hit=True,
                )
            return self._load_artifact(
                artifact_dir,
                expected_cache_key=cache_key,
                expected_source_hash=source_hash,
                cache_hit=False,
            )
        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            raise

    def _load_artifact(
        self,
        artifact_dir: Path,
        *,
        expected_cache_key: Optional[str] = None,
        expected_source_hash: Optional[str] = None,
        cache_hit: bool,
    ) -> SynthesisArtifact:
        manifest_path = artifact_dir / "artifact.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifact = SynthesisArtifact.from_manifest(
                manifest,
                artifact_dir=artifact_dir,
                cache_hit=cache_hit,
            )
        except SynthesisError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError(
                "The synthesis cache manifest is missing or invalid",
                details={"path": str(manifest_path), "reason": str(exc)},
            ) from exc

        if expected_cache_key is not None and artifact.cache_key != expected_cache_key:
            raise ArtifactIntegrityError(
                "The synthesis cache key does not match its request",
                details={
                    "expected": expected_cache_key,
                    "actual": artifact.cache_key,
                },
            )
        if (
            expected_source_hash is not None
            and artifact.source.get("sha256") != expected_source_hash
        ):
            raise ArtifactIntegrityError(
                "The cached source hash does not match its request",
                details={
                    "expected": expected_source_hash,
                    "actual": artifact.source.get("sha256"),
                },
            )

        for path, expected_hash, label in (
            (artifact.circuit_path, artifact.artifact_sha256, "circuit"),
            (artifact.workload_path, artifact.workload_sha256, "workload"),
        ):
            if not path.is_file():
                raise ArtifactIntegrityError(
                    f"The cached {label} file is missing",
                    details={"path": str(path)},
                )
            actual_hash = sha256_file(path)
            if actual_hash != expected_hash:
                raise ArtifactIntegrityError(
                    f"The cached {label} hash does not match its manifest",
                    details={
                        "path": str(path),
                        "expected": expected_hash,
                        "actual": actual_hash,
                    },
                )

        workload = load_artifact_workload(artifact)
        if workload.representation != artifact.representation:
            raise ArtifactIntegrityError(
                "The cached workload representation does not match its manifest",
                details={
                    "manifest": artifact.representation,
                    "workload": workload.representation,
                },
            )
        return artifact

    def load(self, cache_key: str) -> SynthesisArtifact:
        """Load and verify an artifact by cache key."""

        return self._load_artifact(
            self.cache_dir / cache_key,
            expected_cache_key=cache_key,
            cache_hit=True,
        )


def load_artifact_workload(artifact: SynthesisArtifact | Path | str) -> FTCircuit:
    """Load the compiler-facing IR from any synthesis backend's artifact."""

    if isinstance(artifact, SynthesisArtifact):
        path = artifact.workload_path
    else:
        value = Path(artifact)
        path = value / "workload.json" if value.is_dir() else value
    try:
        return FTCircuit.from_json(path.read_text(encoding="utf-8"))
    except SynthesisError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ArtifactIntegrityError(
            "The serialized FTCircuit could not be read",
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def synthesize(
    source: Path | str,
    spec: SynthesisSpec,
    *,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    source_type: str = "path",
    source_name: Optional[str] = None,
) -> SynthesisArtifact:
    """Convenience entrypoint for one synthesis request."""

    return Synthesizer(cache_dir).synthesize(
        source,
        spec,
        source_type=source_type,
        source_name=source_name,
    )
