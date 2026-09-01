from __future__ import annotations

from collections import Counter
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace
import pytest

from heteqsys.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    ResourceMoveDispatchRecipe,
)
from heteqsys.architecture.recipes import ProgramWorkLineage
from heteqsys.architecture.state import (
    ArchitectureState,
    TentativeBinding,
)
from heteqsys.evaluation import (
    BufferSpec,
    EngineSpec,
    EvaluationError,
    EvaluationPolicy,
    ExecutionPlan,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    evaluate,
)
from heteqsys.evaluation.components import (
    BackendRequest,
    CandidateImplementation,
    CompletionRequest,
    EventOutcome,
    PreparedExecution,
    ProgramSchedulingRequest,
    RealizationRequest,
    ResourceSchedulingRequest,
    RuntimeComponentDescriptor,
    RuntimeComponentError,
    RuntimeComponentManifest,
    RuntimeComponentSet,
    RuntimeOperationView,
    build_runtime_component_set,
    default_direct_runtime_component_manifest,
    default_event_engine_descriptor,
    default_runtime_component_manifest,
)
from heteqsys.schema import normalize_json
from heteqsys.compiler.movement import compile_resource_move


def _descriptor(role: str, suffix: str, **config) -> RuntimeComponentDescriptor:
    return RuntimeComponentDescriptor(
        role=role,
        component_id=f"test.{role}.{suffix}.v1",
        provider="tests",
        effective_config=config,
    )


def test_runtime_component_json_freezing_preserves_nested_value_and_detaches() -> None:
    source = {"nested": {"items": [1, 2]}, "enabled": True}
    operation = RuntimeOperationView(
        id=0,
        plane="program",
        opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
        duration_s=1.0,
    )
    candidate = CandidateImplementation(
        candidate_id="program:0",
        operation=operation,
        binding=TentativeBinding(0, {}, {}, {}, {}, {}, {}, {}, {}, {}),
        duration_s=1.0,
        compiler_artifact=source,
        lineage=ProgramWorkLineage("program:0", 0),
    )
    source["nested"]["items"].append(3)

    assert normalize_json(candidate.compiler_artifact) == {
        "enabled": True,
        "nested": {"items": [1, 2]},
    }
    with pytest.raises(TypeError):
        candidate.compiler_artifact["enabled"] = False  # type: ignore[index]
    with pytest.raises(TypeError):
        candidate.compiler_artifact["nested"]["items"] += (3,)  # type: ignore[index]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), bytearray(b"x")])
def test_runtime_component_json_freezing_rejects_invalid_values(invalid) -> None:
    with pytest.raises(RuntimeComponentError, match="finite JSON data"):
        RuntimeComponentDescriptor(
            role="runtime_realizer",
            component_id="test.invalid.v1",
            effective_config={"invalid": invalid},
        )


def test_evaluator_builds_one_detached_view_per_immutable_program_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1.0,
                ),
                ArchitectureInstruction(
                    1,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    predecessor_ids=(0,),
                    duration_s=1.0,
                ),
            )
        ),
        ResourceDAG(()),
        (),
        (),
    )
    original = RuntimeOperationView.from_operation
    calls: list[int] = []

    def track(cls, operation, *, plane):
        calls.append(id(operation))
        return original(operation, plane=plane)

    monkeypatch.setattr(
        RuntimeOperationView,
        "from_operation",
        classmethod(track),
    )

    result = evaluate(plan)

    assert result.completed_program_instructions == 2
    assert Counter(calls) == Counter(
        {id(operation): 1 for operation in plan.program_dag.instructions}
    )


def test_resource_move_binding_cache_is_endpoint_exact_and_token_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import heteqsys.compiler.movement as movement

    compile_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def compile_na(operands, _system, _compiler_spec, _model):
        sources = tuple(operands.source_slots.values())
        destinations = tuple(operands.destination_slots.values())
        compile_calls.append((sources, destinations))
        return 2e-6, {
            "source_coordinates": {"0": [1.0, 2.0]},
            "destination_coordinates": {"0": [3.0, 4.0]},
        }

    monkeypatch.setattr(
        movement,
        "_compute_module",
        lambda _specification: (
            SimpleNamespace(modality="neutral_atom"),
            object(),
        ),
    )
    monkeypatch.setattr(movement, "_compile_na_move", compile_na)

    process = ResourceProcess(
        "move",
        ArchitectureOpcode.MOVE_QUBITS,
        consumes={"source": 1},
        produces={"destination": 1},
        forwards={"source": "destination"},
        deferred_dispatch=ResourceMoveDispatchRecipe(),
    )
    cache = {}

    def compile_one(token: str, destination: str, *, cached: bool = True):
        return compile_resource_move(
            process,
            {"source": (token,)},
            {"source": ("S0",)},
            {"destination": (destination,)},
            object(),
            object(),
            object(),
            binding_cache=cache if cached else None,
        )

    first = compile_one("m0", "D0")
    uncached = compile_one("m0", "D0", cached=False)
    assert first == uncached
    assert len(compile_calls) == 2

    first["source_coordinates"]["0"][0] = 99.0
    second = compile_one("m1", "D0")
    assert len(compile_calls) == 2
    assert second["source_coordinates"] == {"0": [1.0, 2.0]}
    assert "moved_tokens" not in second

    third = compile_one("m2", "D1")
    assert len(compile_calls) == 3
    assert third["destination_coordinates"] == {"0": [3.0, 4.0]}
    assert compile_calls[-1] == (("S0",), ("D1",))


def _base_components() -> RuntimeComponentSet:
    return build_runtime_component_set(
        default_direct_runtime_component_manifest(),
        runtime_instruction_compiler=None,
        runtime_resource_compiler=None,
    )


def _bind(plan: ExecutionPlan, components: RuntimeComponentSet) -> ExecutionPlan:
    provenance = {
        key: value
        for key, value in plan.provenance.items()
        if key != "runtime_manifest_hash"
    }
    return replace(
        plan,
        provenance=provenance,
        runtime_components=components.manifest.to_dict(),
    )


def _component_plan() -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1.0,
                    consumes={"source": 1},
                    metadata={"nested": {"items": [1, 2]}},
                ),
                ArchitectureInstruction(
                    1,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1.0,
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    duration_s=0.4,
                    produces={"destination": 1},
                ),
            )
        ),
        (
            BufferSpec(
                "source",
                2,
                "magic_state",
                slots=("S0", "S1"),
                initial_contents=("m0", "m1"),
            ),
            BufferSpec(
                "destination",
                2,
                "magic_state",
                slots=("D0", "D1"),
            ),
        ),
        (),
    )


class FakeRealizer:
    descriptor = _descriptor("runtime_realizer", "non_fifo_duration")

    def realize(self, request: RealizationRequest) -> CandidateImplementation:
        binding = request.snapshot.propose(request.operation)
        if request.operation.consumes.get("source"):
            binding = replace(
                binding,
                consumed_tokens={"source": ("m1",)},
                consumed_slots={"source": ("S1",)},
            )
        if request.operation.produces.get("destination"):
            buffer = request.snapshot.buffers["destination"]
            occupied = {
                request.snapshot.token_slots[token]
                for token in buffer.ready_tokens
            } | set(buffer.reserved_output_slots)
            selected = next(
                slot for slot in reversed(buffer.slots) if slot not in occupied
            )
            binding = replace(
                binding,
                produced_slots={"destination": (selected,)},
            )
        is_program = request.operation.plane == "program"
        return CandidateImplementation(
            candidate_id=request.candidate_id,
            operation=request.operation,
            binding=binding,
            duration_s=0.8 if is_program else 0.4,
            compiler_artifact={
                "compiler_fake" if is_program else "resolver_fake": True
            },
            lineage=request.lineage,
        )


class ReverseScheduler:
    descriptor = _descriptor("scheduler", "reverse_program")

    def order_program(self, request: ProgramSchedulingRequest):
        return tuple(reversed(request.ready_ids))

    def order_resources(self, request: ResourceSchedulingRequest):
        return request.process_ids


class HalfDurationBackend:
    descriptor = _descriptor("execution_backend", "half_duration")

    def prepare(self, request: BackendRequest) -> PreparedExecution:
        return PreparedExecution(
            duration_s=request.candidate.duration_s / 2,
            artifact={"kind": "fake-profile-artifact", "backend_fake": True},
        )


class FakeOutcome:
    descriptor = _descriptor("outcome_model", "deterministic")

    def resolve(self, request: CompletionRequest) -> EventOutcome:
        return EventOutcome(
            values={
                "bit": request.event_id % 2,
                "artifact_kind": request.backend_artifact.get("kind"),
                "outcome_seed": request.outcome_seed,
            },
            metadata={"source": "fake"},
        )


def _all_fake_components() -> RuntimeComponentSet:
    return RuntimeComponentSet(
        runtime_realizer=FakeRealizer(),
        scheduler=ReverseScheduler(),
        execution_backend=HalfDurationBackend(),
        outcome_model=FakeOutcome(),
    )


def test_four_minimal_runtime_components_are_replaceable_without_engine_changes() -> None:
    components = _all_fake_components()
    plan = _bind(_component_plan(), components)

    result = evaluate(plan, runtime_components=components)
    events = [event.to_dict() for event in result.events]

    assert all(result.invariant_checks.values())
    assert [event["instruction_id"] for event in events[:2]] == [1, 0]
    program_zero = next(event for event in events if event["instruction_id"] == 0)
    assert program_zero["consumed_tokens"] == {"source": ["m1"]}
    assert program_zero["duration_s"] == pytest.approx(0.4)
    assert program_zero["metadata"]["compiler_fake"] is True
    assert program_zero["backend_artifact"] == {
        "backend_fake": True,
        "kind": "fake-profile-artifact",
    }
    assert program_zero["outcome"]["values"]["bit"] in {0, 1}
    assert (
        program_zero["outcome"]["values"]["artifact_kind"]
        == "fake-profile-artifact"
    )
    assert isinstance(program_zero["outcome"]["values"]["outcome_seed"], int)

    first_resource = next(event for event in events if event["plane"] == "resource")
    assert first_resource["produced_slots"] == {"destination": ["D1"]}
    assert first_resource["duration_s"] == pytest.approx(0.2)
    assert first_resource["metadata"]["resolver_fake"] is True
    assert result.runtime_components["manifest_hash"] == components.manifest.manifest_hash
    assert plan.provenance["runtime_manifest_hash"] == components.manifest.manifest_hash


class ReadOnlyRealizer:
    descriptor = _descriptor("runtime_realizer", "read_only_probe")

    def __init__(self) -> None:
        self.rejections = 0

    def realize(self, request: RealizationRequest) -> CandidateImplementation:
        mutations = [
            lambda: request.snapshot.locations.__setitem__("q:0", "bad"),
            lambda: request.operation.consumes.__setitem__("source", 0),
        ]
        if "nested" in request.operation.metadata:
            mutations.append(
                lambda: request.operation.metadata["nested"].__setitem__(
                    "extra", True
                )
            )
        for mutation in mutations:
            with pytest.raises((AttributeError, TypeError)):
                mutation()
            self.rejections += 1
        return CandidateImplementation(
            candidate_id=request.candidate_id,
            operation=request.operation,
            binding=request.snapshot.propose(request.operation),
            duration_s=request.base_duration_s,
            lineage=request.lineage,
        )


class ReadOnlyScheduler:
    descriptor = _descriptor("scheduler", "read_only_probe")

    def __init__(self) -> None:
        self.rejections = 0

    def order_program(self, request: ProgramSchedulingRequest):
        if request.ready_ids:
            with pytest.raises(TypeError):
                request.ready_ids[0] = 99  # type: ignore[index]
            self.rejections += 1
        return request.ready_ids

    def order_resources(self, request: ResourceSchedulingRequest):
        return request.process_ids


def test_component_requests_are_detached_and_deeply_immutable() -> None:
    base = _base_components()
    compiler = ReadOnlyRealizer()
    scheduler = ReadOnlyScheduler()
    components = replace(
        base,
        runtime_realizer=compiler,
        scheduler=scheduler,
    )
    plan = _bind(_component_plan(), components)
    before_hash = plan.plan_hash

    evaluate(plan, runtime_components=components)

    assert compiler.rejections >= 3
    assert scheduler.rejections >= 1
    assert plan.plan_hash == before_hash
    assert plan.program_dag.instructions[0].metadata == {
        "nested": {"items": (1, 2)}
    }


def test_manifest_is_deep_immutable_roundtrippable_and_hash_sensitive() -> None:
    source_config = {"b": 2, "a": {"values": [1, 2]}}
    first = RuntimeComponentDescriptor(
        "execution_backend",
        "test.backend.v1",
        effective_config=source_config,
    )
    second = RuntimeComponentDescriptor(
        "execution_backend",
        "test.backend.v1",
        effective_config={"a": {"values": [1, 2]}, "b": 2},
    )
    source_config["a"]["values"].append(3)

    assert first.component_hash == second.component_hash
    assert first.effective_config["a"]["values"] == (1, 2)
    with pytest.raises(TypeError):
        first.effective_config["b"] = 3  # type: ignore[index]

    default = default_runtime_component_manifest()
    changed_components = dict(default.components)
    changed_components["execution_backend"] = first
    changed = RuntimeComponentManifest(
        components=changed_components,
        event_engine=default.event_engine,
    )
    restored = RuntimeComponentManifest.from_dict(changed.to_dict())

    assert restored.to_dict() == changed.to_dict()
    assert restored.manifest_hash == changed.manifest_hash
    assert changed.manifest_hash != default.manifest_hash

    plan = _component_plan()
    default_plan = replace(
        plan,
        provenance={},
        runtime_components=default.to_dict(),
    )
    changed_plan = replace(
        plan,
        provenance={},
        runtime_components=changed.to_dict(),
    )
    assert default_plan.plan_hash != changed_plan.plan_hash
    assert (
        ExecutionPlan.from_json(changed_plan.to_json()).plan_hash
        == changed_plan.plan_hash
    )


def test_live_manifest_mismatch_is_rejected_before_state_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declared = _base_components()
    live = replace(declared, execution_backend=HalfDurationBackend())
    plan = _bind(_component_plan(), declared)

    def fail_if_state_is_created(cls, _plan):
        raise AssertionError("state creation happened before manifest validation")

    monkeypatch.setattr(
        ArchitectureState,
        "from_plan",
        classmethod(fail_if_state_is_created),
    )
    with pytest.raises(EvaluationError, match="do not match"):
        evaluate(plan, runtime_components=live)


class InvalidScheduler:
    descriptor = _descriptor("scheduler", "duplicates")

    def order_program(self, request: ProgramSchedulingRequest):
        return (request.ready_ids[0], request.ready_ids[0])

    def order_resources(self, request: ResourceSchedulingRequest):
        return request.process_ids


def test_invalid_scheduler_selection_is_rejected_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base_components()
    components = replace(base, scheduler=InvalidScheduler())
    plan = _bind(_component_plan(), components)
    state = ArchitectureState.from_plan(plan)
    before = state.snapshot()
    monkeypatch.setattr(
        ArchitectureState,
        "from_plan",
        classmethod(lambda cls, _plan: state),
    )

    with pytest.raises(EvaluationError, match="duplicate"):
        evaluate(plan, runtime_components=components)

    assert state.snapshot() == before


class FailingOutcome:
    descriptor = _descriptor("outcome_model", "failure")

    def resolve(self, request: CompletionRequest) -> EventOutcome:
        raise RuntimeError(f"outcome failed for {request.candidate_id}")


def test_outcome_failure_keeps_committed_reservation_uncompleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base_components()
    components = replace(base, outcome_model=FailingOutcome())
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    produces={"out": 1},
                    engines={"compute": 1},
                ),
            )
        ),
        ResourceDAG(()),
        (BufferSpec("out", 1, "token"),),
        (EngineSpec("compute"),),
        runtime_components=components.manifest.to_dict(),
    )
    state = ArchitectureState.from_plan(plan)
    monkeypatch.setattr(
        ArchitectureState,
        "from_plan",
        classmethod(lambda cls, _plan: state),
    )

    with pytest.raises(RuntimeError, match="outcome failed"):
        evaluate(plan, runtime_components=components)

    assert state.version == 1
    assert state.buffers["out"].pending_outputs == 1
    assert state.engines["compute"].users == 1
    assert set(state.active_reservations) == {0}


def test_direct_plan_materializes_the_effective_manifest() -> None:
    plan = _component_plan()
    components = _base_components()

    assert (
        RuntimeComponentManifest.from_dict(
            normalize_json(plan.runtime_components)
        ).manifest_hash
        == components.manifest.manifest_hash
    )
    assert (
        plan.provenance["runtime_manifest_hash"]
        == components.manifest.manifest_hash
    )
    result = evaluate(plan)
    assert (
        RuntimeComponentManifest.from_dict(
            normalize_json(result.runtime_components)
        ).manifest_hash
        == components.manifest.manifest_hash
    )


@pytest.mark.parametrize("invalid", (None, [], "reverse", 3))
def test_component_effective_config_must_be_a_mapping(invalid) -> None:
    with pytest.raises(RuntimeComponentError, match="must be a mapping"):
        RuntimeComponentDescriptor(
            "scheduler",
            "test.scheduler.invalid.v1",
            effective_config=invalid,
        )


def test_manifest_parsing_requires_exact_fields_and_mandatory_hashes() -> None:
    descriptor = _descriptor("scheduler", "strict_fields")
    record = descriptor.to_dict()
    record["looks_supported"] = True
    with pytest.raises(RuntimeComponentError, match="Unknown runtime-component"):
        RuntimeComponentDescriptor.from_dict(record)

    complete_record = default_direct_runtime_component_manifest().to_dict()
    canonical = RuntimeComponentManifest.from_dict(complete_record)

    missing_manifest_hash = normalize_json(complete_record)
    missing_manifest_hash.pop("manifest_hash")
    with pytest.raises(RuntimeComponentError, match="Missing runtime-manifest"):
        RuntimeComponentManifest.from_dict(missing_manifest_hash)

    missing_component_hash = normalize_json(complete_record)
    missing_component_hash["event_engine"].pop("component_hash")
    with pytest.raises(RuntimeComponentError, match="Missing runtime-component"):
        RuntimeComponentManifest.from_dict(missing_component_hash)

    with pytest.raises(RuntimeComponentError, match="Missing runtime-manifest"):
        replace(
            _component_plan(),
            provenance={},
            runtime_components=missing_manifest_hash,
        )

    plan = replace(
        _component_plan(),
        provenance={},
        runtime_components=canonical.to_dict(),
    )
    before = plan.plan_hash
    with pytest.raises(TypeError):
        plan.provenance["runtime_manifest_hash"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.initial_locations["q:0"] = "changed"  # type: ignore[index]
    assert plan.plan_hash == before


def test_minimal_runtime_manifest_v3_has_exactly_four_roles_and_rejects_v1() -> None:
    canonical = default_runtime_component_manifest()
    direct = default_direct_runtime_component_manifest()

    assert canonical.to_dict()["schema_version"] == "arqsim.runtime-manifest.v3"
    assert set(canonical.components) == {
        "runtime_realizer",
        "scheduler",
        "execution_backend",
        "outcome_model",
    }
    assert {
        role: descriptor.component_id
        for role, descriptor in canonical.components.items()
    } == {
        "runtime_realizer": "runtime_realizer.state_bound.v2",
        "scheduler": "scheduler.program_first_eager.v1",
        "execution_backend": "backend.profile.v1",
        "outcome_model": "outcome.seeded_bernoulli.v1",
    }
    assert (
        direct.components["runtime_realizer"].component_id
        == "runtime_realizer.direct_fifo_injection.v2"
    )

    legacy_manifest = canonical.to_dict()
    legacy_manifest["schema_version"] = "arqsim.runtime-manifest.v1"
    with pytest.raises(RuntimeComponentError, match="Unsupported runtime-manifest"):
        RuntimeComponentManifest.from_dict(legacy_manifest)

    legacy_plan = _component_plan().to_dict()
    legacy_plan["schema_version"] = "arqsim.execution-plan.v4"
    with pytest.raises(ValueError, match="Unsupported execution-plan schema"):
        ExecutionPlan.from_dict(legacy_plan)


def test_runtime_component_codecs_reject_aliases_omissions_and_tampering() -> None:
    canonical = _descriptor("scheduler", "codec_probe").to_dict()

    mutations = []
    missing = normalize_json(canonical)
    missing.pop("provider")
    mutations.append(missing)
    null_value = normalize_json(canonical)
    null_value["contract_version"] = None
    mutations.append(null_value)
    integer_alias = normalize_json(canonical)
    integer_alias["implementation_version"] = 1
    mutations.append(integer_alias)
    tuple_alias = normalize_json(canonical)
    tuple_alias["effective_config"] = {"values": (1, 2)}
    mutations.append(tuple_alias)
    nonfinite = normalize_json(canonical)
    nonfinite["effective_config"] = {"value": float("nan")}
    mutations.append(nonfinite)
    tampered_hash = normalize_json(canonical)
    tampered_hash["component_hash"] = "0" * 64
    mutations.append(tampered_hash)

    for record in mutations:
        with pytest.raises(RuntimeComponentError):
            RuntimeComponentDescriptor.from_dict(record)

    with pytest.raises(RuntimeComponentError, match="plain dictionaries"):
        RuntimeComponentDescriptor.from_dict(MappingProxyType(canonical))
    with pytest.raises(RuntimeComponentError, match="canonical non-empty string"):
        RuntimeComponentDescriptor(True, "not-a-string-role")

    manifest = default_runtime_component_manifest().to_dict()
    with pytest.raises(RuntimeComponentError, match="plain dictionaries"):
        RuntimeComponentManifest.from_dict(MappingProxyType(manifest))
    forged = normalize_json(manifest)
    forged["manifest_hash"] = "0" * 64
    with pytest.raises(RuntimeComponentError, match="hash"):
        RuntimeComponentManifest.from_dict(forged)


@pytest.mark.parametrize("invalid", (True, "1", Decimal("1")))
def test_minimal_runtime_numeric_seams_reject_coercion(invalid) -> None:
    plan = _component_plan()
    snapshot = ArchitectureState.from_plan(plan).snapshot()
    operation = RuntimeOperationView.from_operation(
        plan.program_dag.instructions[1],
        plane="program",
    )
    binding = snapshot.propose(operation)

    with pytest.raises(RuntimeComponentError):
        CandidateImplementation(
            candidate_id="program:1",
            operation=operation,
            binding=binding,
            duration_s=invalid,
        )
    with pytest.raises(RuntimeComponentError):
        PreparedExecution(duration_s=invalid)
    with pytest.raises(RuntimeComponentError):
        RealizationRequest(
            snapshot=snapshot,
            candidate_id="program:1",
            operation=operation,
            base_duration_s=invalid,
            now_s=0.0,
        )


def test_realization_context_is_exact_typed_and_nonnegative() -> None:
    plan = _component_plan()
    snapshot = ArchitectureState.from_plan(plan).snapshot()
    operation = RuntimeOperationView.from_operation(
        plan.program_dag.instructions[1],
        plane="program",
    )
    base = {
        "snapshot": snapshot,
        "candidate_id": "program:1",
        "operation": operation,
        "base_duration_s": 1.0,
        "now_s": 0.0,
    }

    for changes in (
        {"now_s": -1.0},
        {"now_s": "0"},
        {"instance": True},
        {"instance": -1},
        {"ready_program": [operation]},
        {"ready_program": (object(),)},
    ):
        with pytest.raises(RuntimeComponentError):
            RealizationRequest(**{**base, **changes})


def test_builtin_resolver_rejects_unimplemented_config_kernel_and_callbacks() -> None:
    canonical = default_runtime_component_manifest()
    changed_components = dict(canonical.components)
    scheduler = canonical.components["scheduler"]
    changed_components["scheduler"] = RuntimeComponentDescriptor(
        role=scheduler.role,
        component_id=scheduler.component_id,
        contract_version=scheduler.contract_version,
        implementation_version=scheduler.implementation_version,
        effective_config={"program_order": "reverse"},
        provider=scheduler.provider,
    )
    fake_config = RuntimeComponentManifest(
        components=changed_components,
        event_engine=canonical.event_engine,
    )
    with pytest.raises(RuntimeComponentError, match="exact canonical"):
        build_runtime_component_set(
            fake_config,
            runtime_instruction_compiler=None,
            runtime_resource_compiler=None,
        )

    fake_kernel = RuntimeComponentManifest(
        components=canonical.components,
        event_engine=RuntimeComponentDescriptor(
            "event_engine",
            "test.event_engine.fabricated.v9",
            provider="tests",
        ),
    )
    with pytest.raises(RuntimeComponentError, match="exact canonical"):
        build_runtime_component_set(
            fake_kernel,
            runtime_instruction_compiler=None,
            runtime_resource_compiler=None,
        )

    with pytest.raises(RuntimeComponentError, match="built-in lowering pipeline"):
        build_runtime_component_set(
            canonical,
            runtime_instruction_compiler=lambda *_: {},
            runtime_resource_compiler=lambda *_: {},
        )
    assert canonical.event_engine == default_event_engine_descriptor()


class AliasIdScheduler:
    descriptor = _descriptor("scheduler", "alias_ids")

    def order_program(self, request: ProgramSchedulingRequest):
        return (False, True)

    def order_resources(self, request: ResourceSchedulingRequest):
        return request.process_ids


def test_scheduler_ids_are_type_strict() -> None:
    components = replace(_base_components(), scheduler=AliasIdScheduler())
    plan = _bind(_component_plan(), components)
    with pytest.raises(EvaluationError, match="unknown.*type-mismatched"):
        evaluate(plan, runtime_components=components)


class BooleanDurationRealizer:
    descriptor = _descriptor("runtime_realizer", "bool_duration")

    def realize(self, request: RealizationRequest) -> CandidateImplementation:
        return CandidateImplementation(
            candidate_id=request.candidate_id,
            operation=request.operation,
            binding=request.snapshot.propose(request.operation),
            duration_s=True,
            lineage=request.lineage,
        )


class BooleanDurationBackend:
    descriptor = _descriptor("execution_backend", "bool_duration")

    def prepare(self, request: BackendRequest) -> PreparedExecution:
        return PreparedExecution(
            duration_s=True,
        )


@pytest.mark.parametrize(
    "role, component",
    (
        ("runtime_realizer", BooleanDurationRealizer()),
        ("execution_backend", BooleanDurationBackend()),
    ),
)
def test_boolean_component_duration_is_rejected(role, component) -> None:
    components = replace(_base_components(), **{role: component})
    plan = _bind(_component_plan(), components)
    with pytest.raises(EvaluationError, match="boolean"):
        evaluate(plan, runtime_components=components)


class ControlArtifactRealizer:
    descriptor = _descriptor("runtime_realizer", "control_artifact")

    def realize(self, request: RealizationRequest) -> CandidateImplementation:
        return CandidateImplementation(
            candidate_id=request.candidate_id,
            operation=request.operation,
            binding=request.snapshot.propose(request.operation),
            duration_s=request.base_duration_s,
            compiler_artifact={"resource_wait_s": False},
            lineage=request.lineage,
        )


def test_compiler_artifact_cannot_restate_engine_control() -> None:
    components = replace(
        _base_components(),
        runtime_realizer=ControlArtifactRealizer(),
    )
    plan = _bind(_component_plan(), components)
    with pytest.raises(EvaluationError, match="control fields"):
        evaluate(plan, runtime_components=components)


class FabricatedEngineMetadataBackend:
    descriptor = _descriptor("execution_backend", "fabricated_engine_metadata")

    def prepare(self, request: BackendRequest) -> PreparedExecution:
        return PreparedExecution(
            duration_s=request.candidate.duration_s,
            artifact={"discarded_outputs": {"fabricated": 7}},
        )


def test_backend_artifact_is_observational_not_engine_control() -> None:
    components = replace(
        _base_components(),
        execution_backend=FabricatedEngineMetadataBackend(),
    )
    plan = _bind(_component_plan(), components)
    result = evaluate(plan, runtime_components=components)
    assert result.metrics["buffer_tokens_discarded"] == {}
    assert all("discarded_outputs" not in event.metadata for event in result.events)
    assert all(
        event.backend_artifact == {"discarded_outputs": {"fabricated": 7}}
        for event in result.events
    )


class AlternatingScheduler:
    descriptor = _descriptor("scheduler", "alternating")

    def __init__(self) -> None:
        self.calls = 0

    def order_program(self, request: ProgramSchedulingRequest):
        self.calls += 1
        if self.calls % 2:
            return request.ready_ids
        return tuple(reversed(request.ready_ids))

    def order_resources(self, request: ResourceSchedulingRequest):
        return request.process_ids


def _trace_level_plan(trace_level: str) -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level=trace_level),
        ProgramDAG(
            tuple(
                ArchitectureInstruction(
                    instruction_id,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1.0,
                    engines={"compute": 1},
                )
                for instruction_id in range(4)
            )
        ),
        ResourceDAG(()),
        (),
        (EngineSpec("compute"),),
    )


def test_trace_diagnostics_never_reinvoke_scheduler() -> None:
    event_orders = []
    call_counts = []
    for trace_level in ("summary", "full"):
        scheduler = AlternatingScheduler()
        components = replace(_base_components(), scheduler=scheduler)
        plan = _bind(_trace_level_plan(trace_level), components)
        result = evaluate(plan, runtime_components=components)
        event_orders.append([event.instruction_id for event in result.events])
        call_counts.append(scheduler.calls)

    assert event_orders[0] == event_orders[1]
    assert call_counts[0] == call_counts[1]


def test_evaluate_rejects_removed_legacy_override_keywords() -> None:
    plan = _component_plan()
    for kwargs in (
        {"seed": plan.policy.seed},
        {"scheduler": object()},
        {"resource_dispatch": object()},
        {"runtime_instruction_compiler": lambda *_: {}},
        {"runtime_resource_compiler": lambda *_: {}},
    ):
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            evaluate(plan, **kwargs)
