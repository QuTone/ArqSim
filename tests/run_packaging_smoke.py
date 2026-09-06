"""Build release artifacts and smoke-test an installed wheel outside the repo.

Run from the repository root with::

    python tests/run_packaging_smoke.py

The smoke is offline by default: the temporary virtual environment can see the
caller's already-installed third-party dependencies, while ``arqsim`` itself
must come from the freshly built wheel. Build products and the virtual
environment live under a temporary directory and are deleted on success.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import site
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ENTRIES = (
    "pyproject.toml",
    "MANIFEST.in",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "arqsim",
    "docs",
    "examples",
    "system_cases",
    "tests",
)
REPOSITORY_SOURCE_TREES = ("docs", "examples", "system_cases", "tests")
REPORT_V1_SCHEMA = "arqsim.evaluation-report.v1"
REPORT_V2_SCHEMA = "arqsim.evaluation-report.v2"

EXPECTED_METADATA_REQUIREMENTS = frozenset(
    {
        "networkx>=2.6",
        "pyyaml>=5.4",
        "qiskit>=1.0",
        'matplotlib>=3.7;extra=="test"',
        'matplotlib>=3.7;extra=="visualization"',
        'nwqec<0.2,>=0.1.2;extra=="nwqec"',
        'pytest>=7.4;extra=="test"',
    }
)
FORBIDDEN_PACKAGE_FILES = frozenset(
    {
        "arqsim/architecture/hierarchy.py",
        "arqsim/architecture/layout_policy.py",
        "arqsim/architecture/legacy_adapter.py",
        "arqsim/architecture/profile_compat.py",
        "arqsim/architecture/quantile_layout.py",
        "arqsim/architecture/spec.py",
        "arqsim/operation_profiles/protocol_selection.py",
        "arqsim/qec/configuration.py",
    }
)
FORBIDDEN_CACHE_PARTS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist"}
)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        rendered = " ".join(command)
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {rendered}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed


def _copy_release_source(destination: Path) -> None:
    destination.mkdir(parents=True)
    for relative in SOURCE_ENTRIES:
        source = REPOSITORY_ROOT / relative
        target = destination / relative
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    ".pytest_cache",
                    ".mypy_cache",
                    ".ruff_cache",
                    "*.egg-info",
                    "*.pyc",
                    "*.pyo",
                    "build",
                    "dist",
                ),
            )
        else:
            shutil.copy2(source, target)


def _build_artifacts(source: Path, artifacts: Path) -> tuple[Path, Path]:
    artifacts.mkdir()
    build_script = textwrap.dedent(
        """
        import setuptools.build_meta as backend
        import sys

        output = sys.argv[1]
        print(backend.build_wheel(output))
        print(backend.build_sdist(output))
        """
    )
    _run([sys.executable, "-c", build_script, str(artifacts)], cwd=source)
    wheels = sorted(artifacts.glob("*.whl"))
    sdists = sorted(artifacts.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(
            "Expected exactly one wheel and one sdist, found "
            f"{len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )
    return wheels[0], sdists[0]


def _expected_package_files(source: Path) -> set[str]:
    expected: set[str] = set()
    for path in (source / "arqsim").rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix not in {".py", ".yaml"}:
            continue
        expected.add(path.relative_to(source).as_posix())
    return expected


def _is_release_source_file(path: Path) -> bool:
    return (
        path.is_file()
        and not FORBIDDEN_CACHE_PARTS.intersection(path.parts)
        and not any(part.endswith(".egg-info") for part in path.parts)
        and path.suffix not in {".pyc", ".pyo"}
    )


def _expected_sdist_files(source: Path) -> set[str]:
    expected = _expected_package_files(source)
    expected.update(
        {
            "pyproject.toml",
            "MANIFEST.in",
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
        }
    )
    for tree_name in REPOSITORY_SOURCE_TREES:
        tree = source / tree_name
        expected.update(
            path.relative_to(source).as_posix()
            for path in tree.rglob("*")
            if _is_release_source_file(path)
        )
    return expected


def _assert_no_legacy_or_cache_paths(
    names: set[str],
    *,
    artifact: str,
) -> None:
    forbidden: list[str] = []
    for name in names:
        parts = Path(name).parts
        if FORBIDDEN_CACHE_PARTS.intersection(parts):
            forbidden.append(name)
            continue
        if name.endswith((".pyc", ".pyo")):
            forbidden.append(name)
            continue
        if any(name.endswith(path) for path in FORBIDDEN_PACKAGE_FILES):
            forbidden.append(name)
    if forbidden:
        raise RuntimeError(
            f"{artifact} contains legacy or cache/build paths: {sorted(forbidden)}"
        )


def _normalized_requirement(requirement: str) -> str:
    return re.sub(r"\s+", "", requirement).lower()


def _inspect_wheel(wheel: Path, expected: set[str]) -> set[str]:
    with ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
        metadata_name = next(
            name for name in wheel_names if name.endswith(".dist-info/METADATA")
        )
        entry_points_name = next(
            name for name in wheel_names if name.endswith(".dist-info/entry_points.txt")
        )
        metadata_text = archive.read(metadata_name).decode("utf-8")
        entry_points = archive.read(entry_points_name).decode("utf-8")

    _assert_no_legacy_or_cache_paths(wheel_names, artifact=wheel.name)
    actual_package_files = {
        name
        for name in wheel_names
        if name.startswith("arqsim/") and not name.endswith("/")
    }
    missing_wheel_files = sorted(expected - actual_package_files)
    unexpected_wheel_files = sorted(actual_package_files - expected)
    if missing_wheel_files or unexpected_wheel_files:
        raise RuntimeError(
            "Wheel package contents differ from the release source: "
            f"missing={missing_wheel_files}, unexpected={unexpected_wheel_files}"
        )

    metadata = Parser().parsestr(metadata_text)
    requirements = frozenset(
        _normalized_requirement(value)
        for value in metadata.get_all("Requires-Dist", [])
    )
    if requirements != EXPECTED_METADATA_REQUIREMENTS:
        raise RuntimeError(
            "Wheel dependency metadata differs from the release contract: "
            f"expected={sorted(EXPECTED_METADATA_REQUIREMENTS)}, "
            f"actual={sorted(requirements)}"
        )
    if metadata.get("Name") != "arqsim" or metadata.get("Version") != "0.2.0":
        raise RuntimeError("Wheel name/version metadata is not arqsim 0.2.0")
    if metadata.get("Author") != "Xiang Fang":
        raise RuntimeError("Wheel author metadata is not Xiang Fang")
    if metadata.get("Requires-Python") != ">=3.10":
        raise RuntimeError("Wheel does not retain the Python >=3.10 boundary")
    if metadata.get("License-Expression") != "Apache-2.0":
        raise RuntimeError("Wheel does not contain the Apache-2.0 SPDX expression")
    if "LICENSE" not in metadata.get_all("License-File", []):
        raise RuntimeError("Wheel metadata does not declare LICENSE")
    if metadata.get("Description-Content-Type") != "text/markdown":
        raise RuntimeError("Wheel metadata does not contain the README as Markdown")
    project_urls = set(metadata.get_all("Project-URL", []))
    expected_repository_url = (
        "Repository, https://github.com/QuTone/ArqSim.git"
    )
    if expected_repository_url not in project_urls:
        raise RuntimeError("Wheel metadata does not contain the canonical repository URL")
    if "arqsim = arqsim.cli:main" not in entry_points:
        raise RuntimeError("Wheel does not contain the arqsim console entry point")
    return wheel_names


def _inspect_artifacts(source: Path, wheel: Path, sdist: Path) -> None:
    expected = _expected_package_files(source)
    _inspect_wheel(wheel, expected)

    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
        source_manifest_name = next(
            name for name in names if name.endswith("/arqsim.egg-info/SOURCES.txt")
        )
        source_manifest = archive.extractfile(source_manifest_name)
        if source_manifest is None:
            raise RuntimeError("sdist does not contain its generated source manifest")
        source_manifest_text = source_manifest.read().decode("utf-8")
    roots = {name.split("/", 1)[0] for name in names}
    if len(roots) != 1:
        raise RuntimeError(f"sdist has unexpected roots: {sorted(roots)}")
    root = next(iter(roots))
    normalized = {
        name[len(root) + 1 :]
        for name in names
        if name.startswith(f"{root}/")
    }
    _assert_no_legacy_or_cache_paths(normalized, artifact=sdist.name)
    missing_sdist_files = sorted(_expected_sdist_files(source) - normalized)
    if missing_sdist_files:
        raise RuntimeError(f"sdist is missing release files: {missing_sdist_files}")
    stale_manifest_entries = sorted(
        path
        for path in FORBIDDEN_PACKAGE_FILES
        if path in source_manifest_text
    )
    if stale_manifest_entries:
        raise RuntimeError(
            "sdist source manifest retains legacy package files: "
            f"{stale_manifest_entries}"
        )


def _extract_sdist(sdist: Path, destination: Path) -> Path:
    destination.mkdir()
    destination_root = destination.resolve()
    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if target != destination_root and destination_root not in target.parents:
                raise RuntimeError(f"sdist contains an unsafe path: {member.name}")
            if member.issym() or member.islnk():
                raise RuntimeError(f"sdist contains an unexpected link: {member.name}")
        archive.extractall(destination)
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"sdist extracted to unexpected roots: {roots}")
    return roots[0]


def _build_wheel_from_sdist(root: Path, sdist: Path) -> Path:
    source = _extract_sdist(sdist, root / "sdist-source")
    artifacts = root / "sdist-wheel"
    artifacts.mkdir()
    build_script = textwrap.dedent(
        """
        import setuptools.build_meta as backend
        import sys

        print(backend.build_wheel(sys.argv[1]))
        """
    )
    _run([sys.executable, "-c", build_script, str(artifacts)], cwd=source)
    wheels = sorted(artifacts.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"Expected one wheel rebuilt from sdist, found {len(wheels)}"
        )
    return wheels[0]


def _create_virtual_environment(
    path: Path, *, env: dict[str, str]
) -> tuple[Path, Path, Path]:
    _run(
        [sys.executable, "-m", "venv", str(path)],
        cwd=path.parent,
        env=env,
    )
    scripts = path / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    command = scripts / ("arqsim.exe" if os.name == "nt" else "arqsim")
    purelib = _run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        cwd=path.parent,
        env=env,
    ).stdout.strip()
    return python, command, Path(purelib).resolve()


def _validate_report(path: Path, *, schema_version: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != schema_version:
        raise RuntimeError(f"Unexpected report schema: {payload.get('schema_version')!r}")
    summary = (
        payload.get("results", {}).get("summary", {})
        if schema_version == REPORT_V2_SCHEMA
        else payload.get("summary", {})
    )
    completed = summary.get("completed_program_instructions")
    if type(completed) is not int or completed <= 0:
        raise RuntimeError(f"Unexpected evaluation summary: {summary!r}")
    checks = summary.get("invariant_checks", {})
    failed = sorted(name for name, passed in checks.items() if passed is not True)
    if not checks or failed:
        raise RuntimeError(f"Evaluation invariant failures: {failed or 'missing checks'}")


def _smoke_installed_wheel(root: Path, wheel: Path) -> None:
    root.mkdir()
    run_env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME"):
        run_env.pop(name, None)
    run_env["MPLCONFIGDIR"] = str(root / "matplotlib")
    venv_python, venv_command, installed_site = _create_virtual_environment(
        root / "venv", env=run_env
    )
    smoke = root / "installed-smoke"
    smoke.mkdir()
    pip_env = run_env.copy()
    pip_env.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PIP_NO_CACHE_DIR": "1",
        }
    )
    _run(
        [str(venv_python), "-m", "pip", "install", "--no-deps", str(wheel)],
        cwd=smoke,
        env=pip_env,
    )

    # Nested --system-site-packages environments inherit the base interpreter's
    # sites, not an invoking venv's dependencies. Append the caller's active
    # site directories only after installation, preserving the new wheel's
    # precedence without executing the caller's .pth/editable-install hooks.
    caller_sites = {
        Path(value).resolve()
        for value in (*site.getsitepackages(), site.getusersitepackages())
        if Path(value).is_dir()
    }
    dependency_paths = dict.fromkeys(
        str(Path(value).resolve())
        for value in sys.path
        if value and Path(value).resolve() in caller_sites
    )
    (installed_site / "arqsim_smoke_dependencies.pth").write_text(
        "".join(f"{value}\n" for value in dependency_paths), encoding="utf-8"
    )
    expected_package_path = installed_site / "arqsim" / "__init__.py"

    api_report = smoke / "api-report.json"
    api_script = textwrap.dedent(
        f"""
        import importlib.util
        from pathlib import Path

        expected_package_path = Path({str(expected_package_path)!r})
        package_spec = importlib.util.find_spec("arqsim")
        if (
            package_spec is None
            or package_spec.origin is None
            or Path(package_spec.origin).resolve() != expected_package_path
        ):
            raise RuntimeError(
                f"Expected fresh wheel at {{expected_package_path}}, got {{package_spec}}"
            )
        import arqsim
        from arqsim import (
            EvaluationConfig,
            FTCircuit,
            run_evaluation,
        )
        from arqsim.program import LogicalLayer, LogicalOperation

        package_path = Path(arqsim.__file__).resolve()
        if package_path != expected_package_path:
            raise RuntimeError(f"Imported outside the fresh wheel: {{package_path}}")
        circuit = FTCircuit(
            representation="clifford_t",
            num_qubits=2,
            num_clbits=0,
            layers=(
                LogicalLayer(0, (LogicalOperation("gate", "h", (0,)),)),
                LogicalLayer(
                    1,
                    (
                        LogicalOperation("gate", "s", (0,)),
                    ),
                ),
                LogicalLayer(2, (LogicalOperation("gate", "cx", (0, 1)),)),
                LogicalLayer(3, (LogicalOperation("gate", "t", (1,)),)),
            ),
        )
        report = run_evaluation(circuit, EvaluationConfig(profile_id="2.3"))
        Path({str(api_report)!r}).write_text(report.to_json(), encoding="utf-8")
        """
    )
    _run([str(venv_python), "-c", api_script], cwd=smoke, env=run_env)
    _validate_report(api_report, schema_version=REPORT_V2_SCHEMA)

    qasm = smoke / "small.qasm"
    qasm.write_text(
        textwrap.dedent(
            """\
            OPENQASM 2.0;
            include "qelib1.inc";
            qreg q[2];
            h q[0];
            s q[0];
            cx q[0],q[1];
            t q[1];
            """
        ),
        encoding="utf-8",
    )
    cli_report = smoke / "cli-report.json"
    _run(
        [
            str(venv_command),
            "evaluate",
            str(qasm),
            "--representation",
            "clifford_t",
            "--architecture",
            "2.3",
            "--seed",
            "0",
            "--output",
            str(cli_report),
        ],
        cwd=smoke,
        env=run_env,
    )
    _validate_report(cli_report, schema_version=REPORT_V2_SCHEMA)

    cli_v1_report = smoke / "cli-report-v1.json"
    _run(
        [
            str(venv_command),
            "evaluate",
            str(qasm),
            "--representation",
            "clifford_t",
            "--architecture",
            "2.3",
            "--seed",
            "0",
            "--report-version",
            "v1",
            "--output",
            str(cli_v1_report),
        ],
        cwd=smoke,
        env=run_env,
    )
    _validate_report(cli_v1_report, schema_version=REPORT_V1_SCHEMA)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="arqsim-packaging-smoke-") as temporary:
        root = Path(temporary)
        source = root / "source"
        artifacts = root / "artifacts"
        _copy_release_source(source)
        wheel, sdist = _build_artifacts(source, artifacts)
        _inspect_artifacts(source, wheel, sdist)
        rebuilt_wheel = _build_wheel_from_sdist(root, sdist)
        source_wheel_names = _inspect_wheel(wheel, _expected_package_files(source))
        rebuilt_wheel_names = _inspect_wheel(
            rebuilt_wheel, _expected_package_files(source)
        )
        if source_wheel_names != rebuilt_wheel_names:
            raise RuntimeError("A wheel rebuilt from the sdist has different members")
        _smoke_installed_wheel(root / "source-wheel-smoke", wheel)
        _smoke_installed_wheel(root / "sdist-wheel-smoke", rebuilt_wheel)
        print(
            "PASS: complete release contents, source and sdist wheels, "
            "installed Python API, and installed CLI"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
