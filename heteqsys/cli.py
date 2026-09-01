"""Thin command-line entry point for synthesis and evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


def _key_values(values: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        key, separator, raw = value.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"Overrides require DOTTED_KEY=VALUE: {value!r}")
        key = key.strip()
        if key in result:
            raise ValueError(f"Override was specified twice: {key}")
        result[key] = yaml.safe_load(raw)
    return result


def _error_document(exc: Exception) -> dict[str, Any]:
    details = getattr(exc, "details", {})
    return {
        "schema_version": "arqsim.error.v1",
        "error": {
            "code": str(getattr(exc, "code", "evaluation_failed")),
            "message": str(exc),
            "details": dict(details) if isinstance(details, dict) else {},
        },
    }


def _render_evaluation_report(report: Any, *, report_version: str) -> str:
    """Serialize one native report through the selected public wire boundary."""

    if report_version == "v2":
        return report.to_json()
    if report_version == "v1":
        from heteqsys.report_v1 import render_evaluation_report_v1
        from heteqsys.schema import normalize_json

        return json.dumps(
            normalize_json(render_evaluation_report_v1(report)),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
    raise ValueError(f"Unsupported report version: {report_version!r}")


def _synthesize(argv: list[str]) -> int:
    from heteqsys.program.errors import InputValidationError, SynthesisError
    from heteqsys.synthesizer import SynthesisSpec, Synthesizer
    from heteqsys.synthesizer.service import DEFAULT_CACHE_DIR

    parser = argparse.ArgumentParser(prog="heteqsys synthesize")
    parser.add_argument("source")
    parser.add_argument("--backend", required=True, choices=("nwqec", "precomputed"))
    parser.add_argument("--representation", required=True, choices=("clifford_t", "pbc"))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--output")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--source-type", default="path")
    parser.add_argument("--source-name")
    parser.add_argument("--rz-error-policy")
    parser.add_argument("--epsilon", type=float)
    parser.add_argument("--keep-ccx", action="store_true")
    parser.add_argument("--keep-cx", action="store_true")
    parser.add_argument("--optimize-t-count", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.overwrite and args.output is None:
            raise InputValidationError("--overwrite requires --output")
        artifact = Synthesizer(args.cache_dir).synthesize(
            args.source,
            SynthesisSpec(
                backend=args.backend,
                representation=args.representation,
                rz_error_policy=args.rz_error_policy,
                epsilon=args.epsilon,
                keep_ccx=args.keep_ccx,
                keep_cx=args.keep_cx,
                optimize_t_count=args.optimize_t_count,
            ),
            source_type=args.source_type,
            source_name=args.source_name,
        )
        exported = (
            artifact.export_circuit(args.output, overwrite=args.overwrite)
            if args.output
            else None
        )
    except SynthesisError as exc:
        print(json.dumps(exc.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
        return 2
    payload = artifact.summary_dict()
    if exported is not None:
        payload["exported_circuit"] = str(exported)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _evaluate(argv: list[str]) -> int:
    from heteqsys.api import (
        CANONICAL_FIDELITY_PRESET,
        EvaluationConfig,
        run_evaluation,
    )
    from heteqsys.evaluation import EvaluationPolicy
    from heteqsys.operation_profiles import ArrivalDistribution, OperationLatencyProfile
    from heteqsys.program import FTCircuit, load_ft_workload

    parser = argparse.ArgumentParser(prog="heteqsys evaluate")
    parser.add_argument("program", help="Serialized FTCircuit JSON or synthesized QASM")
    parser.add_argument("--representation", choices=("clifford_t", "pbc"))
    parser.add_argument(
        "--config",
        help="serialized arqsim.evaluation-config.v1 JSON",
    )
    parser.add_argument("--architecture")
    parser.add_argument(
        "--set",
        dest="policy_overrides",
        action="append",
        default=[],
        metavar="DOTTED_KEY=VALUE",
        help="override one architecture layout-policy field",
    )
    parser.add_argument("--magic-rate", type=float)
    parser.add_argument("--bell-rate", type=float)
    parser.add_argument(
        "--arrival", choices=("deterministic", "exponential", "geometric")
    )
    parser.add_argument("--magic-consumption", choices=("bulk_wave", "incremental"))
    parser.add_argument(
        "--store-load-policy",
        choices=("dependency_aware_overlap",),
    )
    parser.add_argument(
        "--trace-level",
        choices=("summary", "full"),
    )
    parser.add_argument("--max-events", type=int)
    parser.add_argument(
        "--fidelity-profile",
        choices=("canonical-reference-v1",),
        help="explicitly enable the named reference fidelity model",
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--report-version",
        choices=("v1", "v2"),
        default="v2",
        help="output schema version (default: v2; v1 is deprecated and static-only)",
    )
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        source = Path(args.program).expanduser().resolve()
        if source.suffix.lower() == ".json":
            circuit = FTCircuit.from_json(source.read_text(encoding="utf-8"))
            if (
                args.representation is not None
                and args.representation != circuit.representation
            ):
                raise ValueError(
                    "--representation does not match the serialized FTCircuit"
                )
        else:
            if args.representation is None:
                raise ValueError("QASM input requires --representation")
            circuit = load_ft_workload(source, args.representation)
        config_flags = {
            "--architecture": args.architecture is not None,
            "--set": bool(args.policy_overrides),
            "--magic-rate": args.magic_rate is not None,
            "--bell-rate": args.bell_rate is not None,
            "--arrival": args.arrival is not None,
            "--magic-consumption": args.magic_consumption is not None,
            "--store-load-policy": args.store_load_policy is not None,
            "--trace-level": args.trace_level is not None,
            "--max-events": args.max_events is not None,
            "--fidelity-profile": args.fidelity_profile is not None,
            "--seed": args.seed is not None,
        }
        conflicts = sorted(name for name, present in config_flags.items() if present)
        if args.config is not None:
            if conflicts:
                raise ValueError(
                    "--config cannot be combined with configuration flags: "
                    + ", ".join(conflicts)
                )
            config_path = Path(args.config).expanduser().resolve()
            evaluation_config = EvaluationConfig.from_json(
                config_path.read_text(encoding="utf-8")
            )
        else:
            facade_defaults = EvaluationConfig()
            default_latency = facade_defaults.latency_profile
            arrival_kind = args.arrival or "deterministic"
            latency = OperationLatencyProfile(
                magic_state_arrival=(
                    ArrivalDistribution.from_rate(
                        args.magic_rate,
                        kind=arrival_kind,
                    )
                    if args.magic_rate is not None
                    else None
                ),
                bell_pair_arrival=(
                    ArrivalDistribution.from_rate(
                        args.bell_rate,
                        kind=arrival_kind,
                    )
                    if args.bell_rate is not None
                    else None
                ),
                derived_arrival_kind=args.arrival,
                gate_duration_s=default_latency.gate_duration_s,
                syndrome_profiles=default_latency.syndrome_profiles,
                compute_protocol_by_modality=(
                    default_latency.compute_protocol_by_modality
                ),
                store_load_protocol=default_latency.store_load_protocol,
                reaction_latency_by_modality_s=(
                    default_latency.reaction_latency_by_modality_s
                ),
                neutral_atom_movement=default_latency.neutral_atom_movement,
                local_magic_delivery_s=default_latency.local_magic_delivery_s,
                link_item_s=default_latency.link_item_s,
                provenance=default_latency.provenance,
            )
            default_policy = facade_defaults.evaluation_policy
            policy = EvaluationPolicy(
                magic_state_consumption=(
                    args.magic_consumption
                    or default_policy.magic_state_consumption
                ),
                store_load_policy=(
                    args.store_load_policy or default_policy.store_load_policy
                ),
                resource_fill_policy=default_policy.resource_fill_policy,
                trace_level=args.trace_level or default_policy.trace_level,
                seed=args.seed if args.seed is not None else default_policy.seed,
                max_events=(
                    args.max_events
                    if args.max_events is not None
                    else default_policy.max_events
                ),
            )
            evaluation_config = EvaluationConfig(
                profile_id=args.architecture or facade_defaults.profile_id,
                layout_policy_overrides=_key_values(args.policy_overrides),
                latency_profile=latency,
                evaluation_policy=policy,
                fidelity_profile=(
                    CANONICAL_FIDELITY_PRESET
                    if args.fidelity_profile == "canonical-reference-v1"
                    else None
                ),
            )
        report = run_evaluation(
            circuit,
            evaluation_config,
        )
        rendered = _render_evaluation_report(
            report,
            report_version=args.report_version,
        )
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(
            json.dumps(_error_document(exc), indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 2

    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "synthesize":
        return _synthesize(arguments[1:])
    if arguments and arguments[0] == "evaluate":
        return _evaluate(arguments[1:])
    parser = argparse.ArgumentParser(prog="heteqsys")
    parser.add_argument("command", choices=("synthesize", "evaluate"))
    parser.parse_args(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
