"""Typed, finite runtime implementation recipes.

The first recipe contract deliberately models only measurement-conditioned
state injection.  It is large enough for T injection and STAR's finite
angle-doubling correction chain, but it is not an arbitrary control-flow DSL.
All possible resource states and the terminal Clifford correction are frozen
in the execution plan before runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from types import MappingProxyType
from typing import Any, Mapping

from arqsim.schema import normalize_json


INJECTION_RECIPE_SCHEMA_VERSION = "arqsim.injection-recipe.v2"


def _plain_string(value: Any, *, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty plain string")
    return value


def _plain_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _strict_fields(
    data: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] = set(),
    label: str,
) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a mapping")
    missing = required - set(data)
    unknown = set(data) - required - optional
    if missing:
        raise ValueError(f"{label} is missing: {sorted(missing)}")
    if unknown:
        raise ValueError(f"Unknown {label} fields: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class ExactPiAngle:
    """One exact angle represented as ``numerator / denominator * pi``."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if type(self.numerator) is not int or type(self.denominator) is not int:
            raise TypeError("Exact-pi angle terms must be plain integers")
        if self.denominator <= 0:
            raise ValueError("Exact-pi angle denominator must be positive")
        reduced = Fraction(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", reduced.numerator)
        object.__setattr__(self, "denominator", reduced.denominator)

    def doubled(self) -> "ExactPiAngle":
        return ExactPiAngle(self.numerator * 2, self.denominator)

    def canonical_modulo_two_pi(self) -> "ExactPiAngle":
        """Return the equivalent angle in the canonical interval ``[-pi, pi]``.

        Exact arithmetic matters here: the terminal Clifford is part of the
        Plan's control contract, so floating-point angle comparison would make
        two semantically identical recipes serialize differently.
        """

        value = Fraction(self.numerator, self.denominator) % 2
        if value > 1:
            value -= 2
        return ExactPiAngle(value.numerator, value.denominator)

    @property
    def key(self) -> str:
        return f"pi*{self.numerator}/{self.denominator}"

    def to_dict(self) -> dict[str, int | str]:
        return {
            "unit": "pi_radians",
            "numerator": self.numerator,
            "denominator": self.denominator,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExactPiAngle":
        required = {"unit", "numerator", "denominator"}
        _strict_fields(data, required=required, label="exact-pi angle")
        if data["unit"] != "pi_radians":
            raise ValueError("Exact-pi angle unit must be 'pi_radians'")
        return cls(data["numerator"], data["denominator"])


class ResourceStateKind(str, Enum):
    T_MAGIC = "t_magic"
    RZ_ANGLE = "rz_angle"


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """One symbolic recipe operand resolved to one installed Plan buffer."""

    ref_id: str
    state_kind: ResourceStateKind | str
    buffer_id: str
    token_kind: str
    quantity: int = 1
    angle: ExactPiAngle | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref_id", _plain_string(self.ref_id, label="ResourceRef ref_id"))
        object.__setattr__(self, "buffer_id", _plain_string(self.buffer_id, label="ResourceRef buffer_id"))
        object.__setattr__(self, "token_kind", _plain_string(self.token_kind, label="ResourceRef token_kind"))
        try:
            state_kind = ResourceStateKind(self.state_kind)
        except ValueError as exc:
            raise ValueError(f"Unsupported resource-state kind: {self.state_kind!r}") from exc
        quantity = _plain_int(self.quantity, label="ResourceRef quantity", minimum=1)
        if state_kind == ResourceStateKind.T_MAGIC and self.angle is not None:
            raise ValueError("A T-magic ResourceRef cannot carry an angle")
        if state_kind == ResourceStateKind.RZ_ANGLE and not isinstance(
            self.angle, ExactPiAngle
        ):
            raise ValueError("An RZ-angle ResourceRef requires an exact angle")
        object.__setattr__(self, "state_kind", state_kind)
        object.__setattr__(self, "quantity", quantity)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ref_id": self.ref_id,
            "state_kind": self.state_kind.value,
            "buffer_id": self.buffer_id,
            "token_kind": self.token_kind,
            "quantity": self.quantity,
        }
        if self.angle is not None:
            result["angle"] = self.angle.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResourceRef":
        required = {"ref_id", "state_kind", "buffer_id", "token_kind", "quantity"}
        _strict_fields(
            data,
            required=required,
            optional={"angle"},
            label="resource reference",
        )
        angle = data.get("angle")
        if angle is not None and not isinstance(angle, Mapping):
            raise ValueError("ResourceRef angle must be a mapping")
        return cls(
            ref_id=data["ref_id"],
            state_kind=data["state_kind"],
            buffer_id=data["buffer_id"],
            token_kind=data["token_kind"],
            quantity=data["quantity"],
            angle=(ExactPiAngle.from_dict(angle) if angle is not None else None),
        )


@dataclass(frozen=True, slots=True)
class InjectionStage:
    """One probabilistic injection attempt in a finite correction chain."""

    index: int
    resource: ResourceRef
    failure_next_stage: int | None = None
    failure_correction: str | None = None

    def __post_init__(self) -> None:
        index = _plain_int(self.index, label="InjectionStage index")
        if not isinstance(self.resource, ResourceRef):
            raise TypeError("InjectionStage resource must be a ResourceRef")
        if (self.failure_next_stage is None) == (self.failure_correction is None):
            raise ValueError(
                "An InjectionStage needs exactly one unfavorable-outcome continuation"
            )
        if self.failure_next_stage is not None:
            next_stage = _plain_int(
                self.failure_next_stage,
                label="InjectionStage failure_next_stage",
            )
            if next_stage != index + 1:
                raise ValueError("Injection stages may continue only to the next stage")
            object.__setattr__(self, "failure_next_stage", next_stage)
        if self.failure_correction is not None:
            object.__setattr__(
                self,
                "failure_correction",
                _plain_string(
                    self.failure_correction,
                    label="InjectionStage failure_correction",
                ).lower(),
            )
        object.__setattr__(self, "index", index)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "index": self.index,
            "resource": self.resource.to_dict(),
        }
        if self.failure_next_stage is not None:
            result["failure_next_stage"] = self.failure_next_stage
        else:
            result["failure_correction"] = self.failure_correction
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InjectionStage":
        _strict_fields(
            data,
            required={"index", "resource"},
            optional={"failure_next_stage", "failure_correction"},
            label="injection stage",
        )
        if not isinstance(data["resource"], Mapping):
            raise ValueError("InjectionStage resource must be a mapping")
        return cls(
            index=data["index"],
            resource=ResourceRef.from_dict(data["resource"]),
            failure_next_stage=data.get("failure_next_stage"),
            failure_correction=data.get("failure_correction"),
        )


@dataclass(frozen=True, slots=True)
class InjectionRecipe:
    """Finite measurement-conditioned implementation for one logical operation."""

    invocation_id: str
    recipe_id: str
    source_layer_index: int
    source_operation_index: int
    qubits: tuple[int, ...]
    stages: tuple[InjectionStage, ...]
    data_mapping: Mapping[int | str, str]
    compute_location: str
    compute_engine: str
    convention: str = "cx_data_magic_measure_magic_z_v1"

    def __post_init__(self) -> None:
        for name in (
            "invocation_id",
            "recipe_id",
            "compute_location",
            "compute_engine",
            "convention",
        ):
            object.__setattr__(
                self,
                name,
                _plain_string(getattr(self, name), label=f"InjectionRecipe {name}"),
            )
        if self.convention != "cx_data_magic_measure_magic_z_v1":
            raise ValueError(f"Unsupported injection convention: {self.convention!r}")
        object.__setattr__(
            self,
            "source_layer_index",
            _plain_int(self.source_layer_index, label="source_layer_index"),
        )
        object.__setattr__(
            self,
            "source_operation_index",
            _plain_int(self.source_operation_index, label="source_operation_index"),
        )
        qubits = tuple(self.qubits)
        if not qubits or any(type(value) is not int or value < 0 for value in qubits):
            raise ValueError("InjectionRecipe qubits must be non-empty non-negative integers")
        if len(set(qubits)) != len(qubits):
            raise ValueError("InjectionRecipe qubits must be unique")
        stages = tuple(self.stages)
        if not stages or any(not isinstance(stage, InjectionStage) for stage in stages):
            raise ValueError("InjectionRecipe requires typed stages")
        if tuple(stage.index for stage in stages) != tuple(range(len(stages))):
            raise ValueError("InjectionRecipe stage indices must be contiguous")
        for stage in stages[:-1]:
            if stage.failure_next_stage != stage.index + 1:
                raise ValueError("Every nonterminal injection stage must continue")
        if stages[-1].failure_correction is None:
            raise ValueError(
                "The final injection stage needs a materialized logical correction"
            )
        state_kinds = {stage.resource.state_kind for stage in stages}
        if state_kinds == {ResourceStateKind.RZ_ANGLE}:
            angle_pools: dict[ExactPiAngle, tuple[str, str]] = {}
            for left, right in zip(stages, stages[1:]):
                assert left.resource.angle is not None
                if right.resource.angle != left.resource.angle.doubled():
                    raise ValueError(
                        "RZ correction stages must follow exact angle doubling"
                    )
            for stage in stages:
                angle = stage.resource.angle
                assert angle is not None
                pool = (stage.resource.buffer_id, stage.resource.token_kind)
                for known_angle, known_pool in angle_pools.items():
                    if angle != known_angle and (
                        pool[0] == known_pool[0] or pool[1] == known_pool[1]
                    ):
                        raise ValueError(
                            "Distinct RZ angles require distinct installed buffer "
                            "and token-kind pools"
                        )
                angle_pools[angle] = pool
            terminal_angle = stages[-1].resource.angle
            assert terminal_angle is not None
            terminal_correction_by_angle = {
                ExactPiAngle(1, 2): "s",
                ExactPiAngle(-1, 2): "sdg",
                ExactPiAngle(1, 1): "z",
            }
            required_correction = terminal_correction_by_angle.get(
                terminal_angle.doubled().canonical_modulo_two_pi()
            )
            if required_correction is None:
                raise ValueError(
                    "The doubled final RZ angle must close to S, Sdg, or Z"
                )
            if stages[-1].failure_correction != required_correction:
                raise ValueError(
                    "The final RZ correction does not match the exact doubled angle"
                )
        elif state_kinds != {ResourceStateKind.T_MAGIC} or len(stages) != 1:
            raise ValueError(
                "A recipe must be one T stage or one finite all-RZ angle chain"
            )
        elif stages[-1].failure_correction != "s":
            raise ValueError("The supported T-injection convention terminates in S")
        mapping: dict[int, str] = {}
        if not isinstance(self.data_mapping, Mapping):
            raise TypeError("InjectionRecipe data_mapping must be a mapping")
        for raw_qubit, raw_slot in self.data_mapping.items():
            try:
                qubit = int(raw_qubit)
            except (TypeError, ValueError) as exc:
                raise ValueError("InjectionRecipe mapping keys must be integers") from exc
            if type(raw_qubit) not in {int, str} or str(qubit) != str(raw_qubit):
                raise ValueError("InjectionRecipe mapping keys must be canonical integers")
            slot = _plain_string(raw_slot, label="InjectionRecipe data slot")
            mapping[qubit] = slot
        if not set(qubits) <= set(mapping):
            raise ValueError("InjectionRecipe data_mapping must cover its qubits")
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("InjectionRecipe data slots must be unique")
        object.__setattr__(self, "qubits", tuple(sorted(qubits)))
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "data_mapping", MappingProxyType(dict(sorted(mapping.items()))))
    @property
    def measurement_registers(self) -> tuple[str, ...]:
        return tuple(
            f"{self.invocation_id}:stage:{stage.index}:bit" for stage in self.stages
        )

    def measurement_register(self, stage_index: int) -> str:
        _plain_int(stage_index, label="stage_index")
        if stage_index >= len(self.stages):
            raise ValueError("Injection stage is outside the finite recipe")
        return self.measurement_registers[stage_index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": INJECTION_RECIPE_SCHEMA_VERSION,
            "kind": "finite_state_injection",
            "invocation_id": self.invocation_id,
            "recipe_id": self.recipe_id,
            "source_layer_index": self.source_layer_index,
            "source_operation_index": self.source_operation_index,
            "qubits": list(self.qubits),
            "stages": [stage.to_dict() for stage in self.stages],
            "data_mapping": {
                str(qubit): slot for qubit, slot in self.data_mapping.items()
            },
            "compute_location": self.compute_location,
            "compute_engine": self.compute_engine,
            "convention": self.convention,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InjectionRecipe":
        required = {
            "schema_version",
            "kind",
            "invocation_id",
            "recipe_id",
            "source_layer_index",
            "source_operation_index",
            "qubits",
            "stages",
            "data_mapping",
            "compute_location",
            "compute_engine",
            "convention",
        }
        _strict_fields(data, required=required, label="injection recipe")
        if data["schema_version"] != INJECTION_RECIPE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported injection-recipe schema: {data['schema_version']!r}"
            )
        if data["kind"] != "finite_state_injection":
            raise ValueError(f"Unsupported implementation recipe kind: {data['kind']!r}")
        if type(data["qubits"]) is not list or type(data["stages"]) is not list:
            raise ValueError("InjectionRecipe qubits/stages must be arrays")
        if not isinstance(data["data_mapping"], Mapping):
            raise ValueError("InjectionRecipe data_mapping must be a mapping")
        return cls(
            invocation_id=data["invocation_id"],
            recipe_id=data["recipe_id"],
            source_layer_index=data["source_layer_index"],
            source_operation_index=data["source_operation_index"],
            qubits=tuple(data["qubits"]),
            stages=tuple(InjectionStage.from_dict(item) for item in data["stages"]),
            data_mapping=data["data_mapping"],
            compute_location=data["compute_location"],
            compute_engine=data["compute_engine"],
            convention=data["convention"],
        )


def build_angle_doubling_recipe(
    *,
    invocation_id: str,
    recipe_id: str,
    source_layer_index: int,
    source_operation_index: int,
    qubits: tuple[int, ...],
    resources: tuple[ResourceRef, ...],
    data_mapping: Mapping[int | str, str],
    compute_location: str,
    compute_engine: str,
) -> InjectionRecipe:
    """Build one finite STAR-style ``theta -> 2theta -> ...`` recipe.

    The caller declares the finite resource inventory.  This helper derives
    stage indices, links, and the terminal Clifford, eliminating the most
    error-prone hand-authored control fields while keeping the contract much
    smaller than a general gadget DSL.
    """

    typed_resources = tuple(resources)
    if not typed_resources or any(
        not isinstance(resource, ResourceRef)
        or resource.state_kind != ResourceStateKind.RZ_ANGLE
        for resource in typed_resources
    ):
        raise ValueError("Angle-doubling recipes require RZ-angle resources")
    for left, right in zip(typed_resources, typed_resources[1:]):
        assert left.angle is not None and right.angle is not None
        if right.angle != left.angle.doubled():
            raise ValueError("Angle-doubling resources must be ordered exactly")
    terminal_angle = typed_resources[-1].angle
    assert terminal_angle is not None
    terminal_correction = {
        ExactPiAngle(1, 2): "s",
        ExactPiAngle(-1, 2): "sdg",
        ExactPiAngle(1, 1): "z",
    }.get(terminal_angle.doubled().canonical_modulo_two_pi())
    if terminal_correction is None:
        raise ValueError(
            "The doubled final RZ angle must close to S, Sdg, or Z"
        )
    stages = tuple(
        InjectionStage(
            index=index,
            resource=resource,
            failure_next_stage=(
                index + 1 if index + 1 < len(typed_resources) else None
            ),
            failure_correction=(
                terminal_correction if index + 1 == len(typed_resources) else None
            ),
        )
        for index, resource in enumerate(typed_resources)
    )
    return InjectionRecipe(
        invocation_id=invocation_id,
        recipe_id=recipe_id,
        source_layer_index=source_layer_index,
        source_operation_index=source_operation_index,
        qubits=qubits,
        stages=stages,
        data_mapping=data_mapping,
        compute_location=compute_location,
        compute_engine=compute_engine,
    )


@dataclass(frozen=True, slots=True)
class ProgramRecipeMember:
    """One recipe invocation participating in a physical Program work item.

    A physical phase may be shared by several logical injections.  Keeping
    membership as a tuple lets the trace record that many-to-one relation
    without duplicating the phase or pretending that it belongs to an
    arbitrary representative invocation.
    """

    recipe_invocation_id: str
    stage_index: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "recipe_invocation_id",
            _plain_string(
                self.recipe_invocation_id,
                label="recipe_invocation_id",
            ),
        )
        object.__setattr__(
            self,
            "stage_index",
            _plain_int(self.stage_index, label="stage_index"),
        )

    @property
    def key(self) -> tuple[str, int]:
        return (self.recipe_invocation_id, self.stage_index)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_invocation_id": self.recipe_invocation_id,
            "stage_index": self.stage_index,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProgramRecipeMember":
        required = {
            "recipe_invocation_id",
            "stage_index",
        }
        _strict_fields(data, required=required, label="Program recipe member")
        return cls(**{name: data[name] for name in required})


@dataclass(frozen=True, slots=True)
class ProgramWorkLineage:
    """Typed causal identity for static and dynamically activated work."""

    work_id: str
    source_instruction_id: int
    parent_event_id: int | None = None
    recipe_members: tuple[ProgramRecipeMember, ...] = ()
    step: str = "source"

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_id", _plain_string(self.work_id, label="work_id"))
        object.__setattr__(
            self,
            "source_instruction_id",
            _plain_int(self.source_instruction_id, label="source_instruction_id"),
        )
        if self.parent_event_id is not None:
            object.__setattr__(
                self,
                "parent_event_id",
                _plain_int(self.parent_event_id, label="parent_event_id"),
            )
        if self.step not in {
            "source",
            "entangle",
            "injection",
            "measurement",
            "reaction",
            "correction",
        }:
            raise ValueError(f"Unsupported Program work step: {self.step!r}")
        members = tuple(self.recipe_members)
        if any(not isinstance(item, ProgramRecipeMember) for item in members):
            raise TypeError(
                "Program work recipe_members must contain ProgramRecipeMember values"
            )
        member_keys = [item.key for item in members]
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("Program work recipe members must be unique")
        if self.step == "source":
            if self.parent_event_id is not None or members:
                raise ValueError(
                    "Source work cannot claim a parent event or recipe members"
                )
        elif self.step == "entangle":
            if self.parent_event_id is not None or not members:
                raise ValueError(
                    "Entangle source work requires recipe members and no parent event"
                )
        else:
            if self.parent_event_id is None or not members:
                raise ValueError(
                    "Continuation work requires a parent event and recipe members"
                )
            if self.step != "measurement" and len(members) != 1:
                raise ValueError(
                    f"Program {self.step} work must belong to exactly one recipe"
                )
        object.__setattr__(self, "recipe_members", members)

    @property
    def recipe_member(self) -> ProgramRecipeMember | None:
        """Return the sole member for single-invocation continuation work."""

        return self.recipe_members[0] if len(self.recipe_members) == 1 else None

    @property
    def recipe_invocation_id(self) -> str | None:
        member = self.recipe_member
        return member.recipe_invocation_id if member is not None else None

    @property
    def stage_index(self) -> int | None:
        member = self.recipe_member
        return member.stage_index if member is not None else None

    def to_dict(self) -> dict[str, Any]:
        return normalize_json(
            {
                "work_id": self.work_id,
                "source_instruction_id": self.source_instruction_id,
                "parent_event_id": self.parent_event_id,
                "recipe_members": [item.to_dict() for item in self.recipe_members],
                "step": self.step,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProgramWorkLineage":
        required = {
            "work_id",
            "source_instruction_id",
            "parent_event_id",
            "recipe_members",
            "step",
        }
        _strict_fields(data, required=required, label="Program work lineage")
        if type(data["recipe_members"]) is not list:
            raise ValueError("Program work lineage recipe_members must be an array")
        return cls(
            work_id=data["work_id"],
            source_instruction_id=data["source_instruction_id"],
            parent_event_id=data["parent_event_id"],
            recipe_members=tuple(
                ProgramRecipeMember.from_dict(item)
                for item in data["recipe_members"]
            ),
            step=data["step"],
        )


__all__ = [
    "ExactPiAngle",
    "INJECTION_RECIPE_SCHEMA_VERSION",
    "InjectionRecipe",
    "InjectionStage",
    "ProgramRecipeMember",
    "ProgramWorkLineage",
    "ResourceRef",
    "ResourceStateKind",
    "build_angle_doubling_recipe",
]
