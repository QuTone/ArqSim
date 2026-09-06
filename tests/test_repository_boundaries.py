from __future__ import annotations

import ast
from graphlib import CycleError, TopologicalSorter
import importlib.util
import inspect
from pathlib import Path
import re
import subprocess
import sys

import pytest


PACKAGE_ROOT = Path(__file__).parents[1] / "arqsim"
PROJECT_ROOT = PACKAGE_ROOT.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
FORBIDDEN_REPOSITORY_ROOTS = {
    "eval",
    "paper_artifact",
    "paper_snapshot",
    "scripts",
}


def _toml_section(name: str) -> str:
    """Return one simple top-level TOML section without a TOML dependency."""

    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^\[{re.escape(name)}\]\s*$\n(.*?)(?=^\[|\Z)",
        text,
    )
    assert match is not None, f"missing [{name}] in pyproject.toml"
    return match.group(1)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = Path(parts[-1]).stem
    return ".".join(("arqsim", *parts))


def _is_type_checking(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == "TYPE_CHECKING"
        or isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "typing"
        and node.attr == "TYPE_CHECKING"
    )


class _ImportTimeCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(node)

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            return
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _trees() -> dict[Path, ast.Module]:
    return {
        path: ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def test_installable_package_does_not_import_repository_workflows() -> None:
    violations: list[str] = []
    for path, tree in _trees().items():
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Call) and node.args:
                dynamic = (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "__import__"
                    or isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                    and node.func.attr == "import_module"
                )
                if dynamic and isinstance(node.args[0], ast.Constant) and isinstance(
                    node.args[0].value, str
                ):
                    names.append(node.args[0].value)
            for name in names:
                if name.split(".", 1)[0] in FORBIDDEN_REPOSITORY_ROOTS:
                    violations.append(
                        f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}: {name}"
                    )
    assert not violations, "repository-only imports found:\n" + "\n".join(violations)


def test_import_time_package_graph_is_acyclic() -> None:
    trees = _trees()
    modules = {_module_name(path): path for path in trees}
    graph: dict[str, set[str]] = {module: set() for module in modules}

    def known_module(name: str) -> str | None:
        candidate = name
        while candidate.startswith("arqsim"):
            if candidate in modules:
                return candidate
            if "." not in candidate:
                break
            candidate = candidate.rpartition(".")[0]
        return None

    for path, tree in trees.items():
        owner = _module_name(path)
        package = owner if path.name == "__init__.py" else owner.rpartition(".")[0]
        collector = _ImportTimeCollector()
        collector.visit(tree)
        for node in collector.imports:
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif node.level:
                relative = "." * node.level + (node.module or "")
                resolved = importlib.util.resolve_name(relative, package)
                targets.append(resolved)
                if node.module is None:
                    targets.extend(
                        f"{resolved}.{alias.name}" for alias in node.names
                    )
            elif node.module:
                targets.append(node.module)
            for target in targets:
                dependency = known_module(target)
                if dependency is not None and dependency != owner:
                    graph[owner].add(dependency)

    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        pytest.fail(f"import-time package dependency cycle: {exc.args[1]}")


def test_release_metadata_matches_the_public_package_contract() -> None:
    import arqsim

    project = _toml_section("project")
    name = re.search(r'(?m)^name\s*=\s*"([^"]+)"\s*$', project)
    version = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project)

    assert name is not None and name.group(1) == "arqsim"
    assert version is not None and version.group(1) == arqsim.__version__
    assert re.search(r'(?m)^readme\s*=.*README\.md', project)
    assert re.search(r'(?m)^requires-python\s*=\s*">=3\.10"\s*$', project)

    scripts = _toml_section("project.scripts")
    assert re.search(
        r'(?m)^arqsim\s*=\s*"arqsim\.cli:main"\s*$', scripts
    )
    package_discovery = _toml_section("tool.setuptools.packages.find")
    declared_packages = set(re.findall(r'"(arqsim[^\"]*)"', package_discovery))
    actual_packages = {
        ".".join(path.parent.relative_to(PROJECT_ROOT).parts)
        for path in PACKAGE_ROOT.rglob("__init__.py")
    }
    assert declared_packages == actual_packages
    assert "namespaces = false" in package_discovery


def test_declared_runtime_package_data_exists_in_the_source_tree() -> None:
    package_data = _toml_section("tool.setuptools.package-data")
    expected_patterns = (
        *(
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in sorted((PACKAGE_ROOT / "architecture/gallery").glob("*/profile.yaml"))
        ),
        "qec/protocol_profiles/*.yaml",
        "operation_profiles/fidelity_profiles/*.yaml",
    )

    for pattern in expected_patterns:
        assert f'"{pattern}"' in package_data
        assert list(PACKAGE_ROOT.glob(pattern)), (
            f"declared runtime package data has no source matches: {pattern}"
        )


def test_legacy_architecture_and_default_qec_construction_paths_are_deleted() -> None:
    import arqsim.architecture as architecture
    import arqsim.operation_profiles as operation_profiles
    import arqsim.qec as qec

    removed_paths = (
        PACKAGE_ROOT / "architecture" / "compact.py",
        PACKAGE_ROOT / "architecture" / "hierarchy.py",
        PACKAGE_ROOT / "architecture" / "instantiate.py",
        PACKAGE_ROOT / "architecture" / "layout_policies",
        PACKAGE_ROOT / "architecture" / "layout_policy.py",
        PACKAGE_ROOT / "architecture" / "legacy_adapter.py",
        PACKAGE_ROOT / "architecture" / "profile_compat.py",
        PACKAGE_ROOT / "architecture" / "quantile_layout.py",
        PACKAGE_ROOT / "architecture" / "spec.py",
        PACKAGE_ROOT / "architecture" / "specification_templates",
        PACKAGE_ROOT / "architecture" / "templates",
        PACKAGE_ROOT / "operation_profiles" / "protocol_selection.py",
        PACKAGE_ROOT / "qec" / "configuration.py",
        PACKAGE_ROOT / "qec" / "configurations",
    )
    assert all(not path.exists() for path in removed_paths)

    package_data = _toml_section("tool.setuptools.package-data")
    assert "architecture/layout_policies" not in package_data
    assert "architecture/specification_templates" not in package_data
    assert "qec/configurations" not in package_data

    for name in (
        "ArchitectureParameters",
        "ArchitectureTemplate",
        "ArchitectureTemplateNotFoundError",
        "ArchitectureLayoutPlan",
        "ArchitectureLayoutPolicy",
        "ArchitectureSpec",
        "BusSpec",
        "COMPACT_PROFILE_SCHEMA_VERSION",
        "ExplicitLayoutPolicy",
        "LAYOUT_PLAN_SCHEMA_VERSION",
        "LinkEndpoint",
        "LinkSpec",
        "MicroArchitectureSpec",
        "ModulePortRef",
        "ModulePortSpec",
        "ModuleSpec",
        "NodeInterfaceSpec",
        "NodeSpec",
        "ParameterConstraint",
        "ParameterDefinition",
        "QUANTILE_LAYOUT_POLICY_SCHEMA_VERSION",
        "QuantileLayoutPolicy",
        "SPEC_SCHEMA_VERSION",
        "SubmoduleLayout",
        "SubmoduleSpec",
        "WorkflowLayoutContext",
        "connection_between",
        "get_architecture_template",
        "instantiate_architecture",
        "instantiate_architecture_spec",
        "list_architecture_templates",
        "load_template_file",
        "load_quantile_layout_policy",
        "logical_bell_buffer_capacities",
        "logical_bell_engine_profiles",
        "module_by_role",
        "modules_by_role",
    ):
        assert not hasattr(architecture, name)
    for name in (
        "QECBinding",
        "QECConfiguration",
        "QEC_SCHEMA_VERSION",
        "RESOLVED_SYSTEM_SCHEMA_VERSION",
        "ResolvedFTSystemSpec",
        "get_default_qec_configuration",
        "load_qec_configuration",
        "resolve_ft_system",
    ):
        assert not hasattr(qec, name)
    for name in (
        "MAGIC_STATE_PROTOCOL_ALIASES",
        "RESOURCE_PROTOCOL_SELECTION_SCHEMA_VERSION",
        "hydrate_resource_protocol_policy",
    ):
        assert not hasattr(operation_profiles, name)

    orchestration = (PACKAGE_ROOT / "specification.py").read_text(
        encoding="utf-8"
    )
    assert "get_architecture_template" not in orchestration
    assert "_construction_profile" not in orchestration
    assert "_interconnect_expansion" not in orchestration
    assert "_template_parameters" not in orchestration


def test_architecture_core_does_not_depend_on_the_bundled_gallery() -> None:
    core_modules = (
        "construction.py",
        "logical_layout.py",
        "logical_layout_policy.py",
        "profile.py",
        "resolver.py",
        "sizing.py",
        "specification.py",
    )
    violations: list[str] = []
    for name in core_modules:
        path = PACKAGE_ROOT / "architecture" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports = (node.module or "",)
            else:
                continue
            if any(
                imported == "gallery"
                or imported.startswith("gallery.")
                or imported == "arqsim.architecture.gallery"
                or imported.startswith("arqsim.architecture.gallery.")
                for imported in imports
            ):
                violations.append(f"{name}:{node.lineno}: {imports}")

    assert not violations, "architecture core imports gallery:\n" + "\n".join(
        violations
    )


def test_minimal_runtime_source_has_no_replaced_component_chain() -> None:
    from arqsim import evaluation
    from arqsim.evaluation import components

    assert components.RUNTIME_COMPONENT_ROLES == (
        "runtime_realizer",
        "scheduler",
        "execution_backend",
        "measurement_provider",
    )
    assert tuple(components.RuntimeComponentSet.__dataclass_fields__) == (
        "runtime_realizer",
        "scheduler",
        "execution_backend",
        "measurement_provider",
    )
    for deleted_name in (
        "BindingPolicy",
        "BindingRequest",
        "CompilationRequest",
        "RuntimeCompiler",
        "ResourceResolver",
        "FifoFirstFreeBindingPolicy",
        "CallbackRuntimeCompiler",
        "CallbackResourceResolver",
        "build_legacy_runtime_component_set",
    ):
        assert not hasattr(components, deleted_name)
    for internal_callback_builder in (
        "build_runtime_instruction_compiler",
        "build_runtime_resource_compiler",
    ):
        assert not hasattr(evaluation, internal_callback_builder)


def test_dead_wrappers_registries_and_runtime_overrides_are_deleted() -> None:
    from arqsim.architecture.state import ArchitectureState
    from arqsim.evaluation import evaluate

    removed_paths = (
        PACKAGE_ROOT / "evaluation" / "scheduler.py",
        PACKAGE_ROOT / "operation_profiles" / "catalog.py",
        PACKAGE_ROOT / "program" / "benchmark.py",
        PACKAGE_ROOT / "qec" / "code.py",
    )
    assert all(not path.exists() for path in removed_paths)

    assert not hasattr(ArchitectureState, "check_start")
    assert not hasattr(ArchitectureState, "eager_batch_size")
    assert tuple(inspect.signature(evaluate).parameters) == (
        "plan",
        "runtime_components",
    )

    lowering = (PACKAGE_ROOT / "evaluation" / "lowering.py").read_text(
        encoding="utf-8"
    )
    layout = (PACKAGE_ROOT / "program" / "layout.py").read_text(
        encoding="utf-8"
    )
    assert "def build_execution_plan(" not in lowering
    assert "def workload_layout_statistics(" not in layout


def test_runtime_dependencies_match_import_ownership() -> None:
    project = _toml_section("project")
    dependencies = re.search(
        r"(?ms)^dependencies\s*=\s*\[(.*?)^\]",
        project,
    )
    assert dependencies is not None
    declared = dependencies.group(1).lower()
    assert "networkx" in declared
    assert "pyyaml" in declared
    assert "qiskit" in declared
    assert all(name not in declared for name in ("numpy", "pandas", "scipy", "matplotlib"))

    visualization = _toml_section("project.optional-dependencies")
    assert re.search(
        r'(?ms)^visualization\s*=\s*\[.*?"matplotlib>=3\.7".*?^\]',
        visualization,
    )


def test_readme_python_quickstart_runs_through_the_public_facade() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    example = PROJECT_ROOT / "examples" / "quickstart.py"

    assert "from arqsim import" in readme
    assert "arqsim evaluate" in readme
    assert example.is_file()
    quickstart = re.search(
        r"(?ms)^## Python quickstart\s+.*?^```python\s*$\n(.*?)^```\s*$",
        readme,
    )
    assert quickstart is not None, "README must contain a Python quickstart fence"
    completed = subprocess.run(
        [sys.executable, "-c", quickstart.group(1)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    rows = [line.split(":", 1) for line in completed.stdout.splitlines()]
    assert [row[0] for row in rows] == [
        "Latency (s)", "Physical qubits", "Success probability"
    ]
    values = [float(row[1]) for row in rows]
    assert all(value > 0 for value in values[:2])
    assert 0 <= values[2] <= 1


def test_local_markdown_links_resolve_inside_the_repository() -> None:
    documents = [
        PROJECT_ROOT / "README.md",
        *PROJECT_ROOT.glob("frontend/README.md"),
        *PROJECT_ROOT.glob("server/README.md"),
        *PROJECT_ROOT.glob("server/benchmark/README.md"),
        *sorted((PROJECT_ROOT / "arqsim").rglob("README.md")),
        *sorted((PROJECT_ROOT / "docs").rglob("*.md")),
        *sorted((PROJECT_ROOT / "examples").rglob("*.md")),
        *sorted((PROJECT_ROOT / "system_cases").rglob("README.md")),
        *sorted((PROJECT_ROOT / "tests" / "fixtures").rglob("README.md")),
    ]
    missing: list[str] = []
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    for document in documents:
        for raw_target in link_pattern.findall(document.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (document.parent / target).resolve()
            try:
                resolved.relative_to(PROJECT_ROOT.resolve())
            except ValueError:
                missing.append(f"{document.relative_to(PROJECT_ROOT)} -> {raw_target}")
                continue
            if not resolved.exists():
                missing.append(f"{document.relative_to(PROJECT_ROOT)} -> {raw_target}")

    assert not missing, "broken or out-of-repository Markdown links:\n" + "\n".join(missing)
