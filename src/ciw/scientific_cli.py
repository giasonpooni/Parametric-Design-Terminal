"""Scientific subcommands over existing NET Session/Workbench operations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .adapters.protocol import AdapterRefusal
from .control_contracts import load
from . import scientific


def _show(value: dict, as_json: bool):
    if as_json:
        print(json.dumps(value, indent=2, allow_nan=False))
        return
    if value.get("schema") == "ciw.scientific-navigation.v1":
        for row in value["operations"]:
            print(f"{row['source_kind']}: {', '.join(row['instrument_highlights'])}")
            print(f"  {row['operation_id']} | {', '.join(row['capabilities'])} | bound here: {row['available']}")
        print("Catalog listing does not execute or qualify a provider.")
    elif value.get("schema") == "ciw.scientific-state-selection.v1":
        context, state = value["context"], value["state"]
        print(f"{context['operation_id']} / {context['stage']} / {context['result_id']}")
        print(f"Frame: {state['frame']}; acquisition: {context['observed_at']}")
        for key, field in state["variables"].items():
            print(f"  {key}: {field['value']} {field['unit']}")
        print("Full covariance: " + json.dumps(state["uncertainty"]["matrix"]))
        print("Native diagnostics: " + json.dumps(context["diagnostics"]))
        print("Projection only; no new execution, physical validation or state admission.")
    else:
        print(json.dumps(value, indent=2, allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="net science", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("catalog")
    command.add_argument("capability", nargs="?")
    command.add_argument("--json", action="store_true")
    command = commands.add_parser("observations")
    command.add_argument("--workspace", type=Path, required=True)
    command.add_argument("--workspace-sha256", required=True)
    command.add_argument("--bundle", required=True)
    command.add_argument("--stage", choices=["predicted", "posterior", "measurement"], required=True)
    command.add_argument("--entity", required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--json", action="store_true")
    for action in ("run", "replay", "inspect", "state", "study", "replay-study"):
        command = commands.add_parser(action)
        command.add_argument("--workspace", type=Path, required=True)
        command.add_argument("--json", action="store_true")
        if action in {"run", "replay", "study", "replay-study"}:
            host_bindings = command.add_mutually_exclusive_group()
            host_bindings.add_argument("--binding", action="append", default=[], metavar="ROLE=ABSOLUTE_PATH")
            host_bindings.add_argument("--bindings-file", type=Path,
                help="Explicit operator provisioning file; retained workspace paths never activate providers")
            command.add_argument("--output-dir", type=Path, required=True)
        if action == "run":
            command.add_argument("--kind", required=True)
            command.add_argument("--source", type=Path, required=True)
            command.add_argument("--label", required=True)
            command.add_argument("--upstream-bundle")
            command.add_argument("--configuration", type=Path)
        if action in {"inspect", "state", "replay", "study"}:
            command.add_argument("--bundle", required=action in {"replay", "study"})
        if action == "inspect":
            command.add_argument("--instrument")
            command.add_argument("--study", type=Path, help="Inspect an existing retained curved-path study against this workspace")
        if action == "replay-study":
            command.add_argument("--study", type=Path, required=True)
        if action == "study":
            command.add_argument("--headings", type=float, nargs="+", required=True)
            command.add_argument("--sample-index", type=int, required=True)
            command.add_argument("--max-lateral", type=float, required=True)
            command.add_argument("--max-heading", type=float, required=True)
            command.add_argument("--length-unit", choices=["m", "mm", "normalized_length"], required=True)
            command.add_argument("--study-id", required=True)
        if action == "state":
            command.add_argument("--result", required=True)
            command.add_argument("--stage", required=True, choices=["prior", "posterior", "reconciled", "output_covariance"])
            command.add_argument("--entity", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            _show(scientific.catalog(args.capability), args.json)
            return 0
        if args.command == "observations":
            from .scientific_observations import export_observations
            from .control_contracts import save_new
            value = export_observations(args.workspace, expected_sha256=args.workspace_sha256,
                bundle_id=args.bundle, stage=args.stage, entity_id=args.entity)
            save_new(args.output, value)
            _show(value if args.json else {"schema": value["schema"], "stage": value["stage"],
                "samples": len(value["stream"]["observations"]), "output": str(args.output),
                "record_digest": value["record_digest"], "authority": value["authority"]}, args.json)
            return 0
        binding_record = None
        bindings = scientific.parse_bindings(args.binding) if args.command in {"run", "replay", "study", "replay-study"} else None
        if getattr(args, "bindings_file", None) is not None:
            from .operator_provision import load_bindings_file
            selected_kind = args.kind if args.command == "run" else "curved-path-transfer" if args.command in {"study", "replay-study"} else None
            binding_record = load_bindings_file(args.bindings_file, workflow=selected_kind)
            bindings = binding_record["bindings"]
        # Source preflight and exact bytes are frozen before output or provider setup.
        if args.command == "run":
            raw = scientific.read_source(args.source)
            scientific.source_payload(args.kind, raw, args.label)
            config = load(args.configuration) if args.configuration else None
        if args.command == "study":
            from .control_plane import ParameterSpace, Interval
            domain = ParameterSpace({"initial_heading_radian": Interval(-0.1, 0.1, "radian")})
            study_request = scientific.heading_request(args.bundle, domain, args.headings,
                sample_index=args.sample_index, study_id=args.study_id,
                limits={"max_abs_lateral": args.max_lateral, "max_abs_heading": args.max_heading,
                        "units": {"length": args.length_unit, "angle": "radian"}})
        with scientific.open_workspace(args.workspace, getattr(args, "output_dir", None)) as session:
            if binding_record is not None and args.command == "replay":
                original = scientific.selected_bundle(session, args.bundle)
                if binding_record["workflow"] != original["kind"]:
                    raise ValueError("Operator bindings select a different retained workflow")
            if args.command == "run":
                value = scientific.execute(session, args.kind, raw, label=args.label, repositories=bindings,
                                           upstream_bundle_id=args.upstream_bundle, configuration=config)
            elif args.command == "replay":
                value = scientific.replay(session, args.bundle, repositories=bindings)
            elif args.command == "replay-study":
                from .curved_path_study import load_study, replay_study, save_study
                original = load_study(args.study, session.workbench)
                scientific.bind(session, "curved-path-transfer", bindings)
                value = replay_study(session.workbench, original)
                save_study(args.output_dir / "study.json", session.workbench, value)
            elif args.command == "study":
                from .curved_path_study import save_study
                value = scientific.heading_study(session, study_request, repositories=bindings)
                save_study(args.output_dir / "study.json", session.workbench, value)
            elif args.command == "inspect" and args.study is not None:
                if args.bundle is not None or args.instrument is not None:
                    raise ValueError("Study inspection cannot also select a bundle/instrument")
                from .curved_path_study import load_study
                value = load_study(args.study, session.workbench)
            elif args.command == "inspect":
                value = scientific.inspect(session, bundle_id=args.bundle, instrument=args.instrument)
            else:
                from .scientific_state import select_state
                value = select_state(session, result_id=args.result, stage=args.stage,
                                     entity_id=args.entity, bundle_id=args.bundle)
        _show(value, args.json)
        return 0
    except (AdapterRefusal, OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(json.dumps({"status": "refused", "code": getattr(exc, "code", "invalid_input"),
                          "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
