"""NET control CLI. Existing `ciw` commands and Session remain unchanged."""
from __future__ import annotations

import argparse
from importlib import import_module
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile

from .operator_commands import COMMANDS


def _registry(path: Path | None, *, bind: bool = False) -> CapabilityRegistry:
    from .control_contracts import load, keys
    from .control_plane import CapabilityRegistry, builtin_registry
    if path is None:
        return builtin_registry(bind=bind)
    if bind:
        raise ValueError("A saved catalog never binds executable providers")
    from .adapters.protocol import InstrumentManifest
    value = load(path)
    keys(value, {"schema", "authorizes_execution", "providers", "operations"})
    if value["schema"] != "ciw.provider-catalog.v1" or value["authorizes_execution"] is not False:
        raise ValueError("Unsupported capability catalog or authority claim")
    if type(value["providers"]) is not dict or type(value["operations"]) is not dict:
        raise ValueError("Provider catalog requires objects")
    registry = CapabilityRegistry()
    for name, manifest in value["providers"].items():
        parsed = InstrumentManifest.from_dict(manifest)
        if name != parsed.instrument_id:
            raise ValueError("Provider catalog identity mismatch")
        selected = {op: item for op, item in value["operations"].items() if item.get("provider") == name}
        if not selected:
            raise ValueError("Provider has no declared operation contracts")
        for item in selected.values():
            keys(item, {"provider", "runtime", "capabilities", "inputs", "outputs", "bound"})
            if type(item["bound"]) is not bool:
                raise ValueError("Invalid saved binding flag")
        runtime = next(iter(selected.values()))["runtime"]
        if any(item["runtime"] != runtime for item in selected.values()):
            raise ValueError("One manifest must describe one pinned runtime")
        registry.advertise(parsed, runtime=runtime,
            capabilities={op: item["capabilities"] for op, item in selected.items()},
            inputs={op: item["inputs"] for op, item in selected.items()},
            outputs={op: item["outputs"] for op, item in selected.items()})
    if set(value["operations"]) != set(registry.catalog()["operations"]):
        raise ValueError("Catalog operation references an undeclared provider")
    return registry


def inspect_path(path: Path, kind: str = "auto") -> dict:
    from .control_contracts import load, save_new
    from .control_checks import inspect_record
    value = load(path)
    if type(value) is dict and "workspace_version" in value:
        if kind not in {"auto", "workspace", "run"}:
            raise ValueError("Requested kind differs from workspace")
        from .session import Session
        # Freeze the single bounded read. Session reopening only writes into scratch.
        with tempfile.TemporaryDirectory(prefix="net-inspect-") as directory:
            root = Path(directory)
            source = root / "workspace.json"
            save_new(source, value)
            session = Session.from_workspace(source, output_dir=root / "reopened")
            return {"schema": "ciw.workspace-inspection.v1", "session": session.snapshot(),
                    "executions": deepcopy(session.executions), "results": deepcopy(session.results),
                    "physical_validation": "not_performed", "state_admission": "not_performed",
                    "reproducibility": "not_established_by_inspection"}
    result = inspect_record(value)
    if kind != "auto" and value["schema"] != f"ciw.{kind}.v1":
        raise ValueError("Requested kind differs from retained schema")
    return result


def _show(value: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, allow_nan=False))
        return
    if value.get("schema") == "ciw.provider-catalog.v1":
        for name, manifest in value["providers"].items():
            print(f"Provider: {name} / version {manifest['version']}")
        for name, item in value["operations"].items():
            print(f"  {name}: {', '.join(item['capabilities'])}; executable bound: {item['bound']}")
        if not value["operations"]:
            print("No matching declared capability. No provider was executed.")
        return
    print(f"Schema: {value.get('schema', 'summary')}")
    original = value.get("record", value)
    for key in ("record_digest", "experiment_id", "model_id", "identity", "clock", "frame", "outcome", "status", "sha256", "size_bytes", "media_type", "producer"):
        if key in original:
            print(f"{key}: {json.dumps(original[key], allow_nan=False)}")
    if "variables" in original:
        for name, field in original["variables"].items():
            print(f"  {name}: {json.dumps(field['value'])} {field['unit']}")
    if original.get("uncertainty") is not None:
        print("Full covariance: " + json.dumps(original["uncertainty"], allow_nan=False))
    if "observations" in original:
        print(f"Observations: {len(original['observations'])}")
    if original.get("schema") == "ciw.thermal-observation-view.v1":
        print(f"Stage: {original['stage']}; coordinates: {', '.join(original['coordinate_order'])}")
        print(f"Observations: {len(original['stream']['observations'])}; units: K")
        print("Source: synthetic; covariance: per-tick marginals, not joint across ticks")
        print("Workspace binding: " + original["binding"]["workspace_sha256"])
    if original.get("schema") == "ciw.check-report.v1":
        print(f"Check suite: {original['plan']['suite_id']} / {original['summary']['status']}")
        for check in original["checks"]:
            print(f"  {check['status']}: {check['name']}")
    if "session" in value:
        print(f"Session: {value['session']['session_id']}; executions: {len(value['executions'])}; results: {len(value['results'])}")
    print("Physical validation: not performed; reproducibility is not established by inspection.")


def _node(name: str, operation: str, dependencies: list[str] | None = None) -> dict:
    return {"node_id": name, "operation_id": operation, "parameters": {"channel": "q"},
            "inputs": {}, "depends_on": dependencies or []}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["foundry", "childhood"]:
        from .foundry_childhood import main as childhood_main
        return childhood_main(argv[2:])
    for name, module, _purpose in COMMANDS:
        if argv and argv[0] == name:
            command_main = import_module("." + module, __package__).main
            return command_main(argv if name == "legibility" else argv[1:])
    parser = argparse.ArgumentParser(prog="net", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, _module, purpose in COMMANDS:
        commands.add_parser(name, help=purpose)
    for name in ("providers", "capabilities"):
        command = commands.add_parser(name)
        command.add_argument("--catalog", type=Path)
        command.add_argument("--json", action="store_true")
        if name == "capabilities":
            command.add_argument("capability", nargs="?")
    command = commands.add_parser("inspect")
    command.add_argument("target", nargs="+", help="FILE, or KIND FILE; provider PROVIDER_ID")
    command.add_argument("--catalog", type=Path)
    command.add_argument("--json", action="store_true")
    command = commands.add_parser("compare")
    command.add_argument("left", type=Path)
    command.add_argument("right", type=Path)
    command.add_argument("--atol", required=True, type=float)
    command.add_argument("--rtol", default=0.0, type=float)
    command.add_argument("--output", type=Path)
    command.add_argument("--json", action="store_true")
    command = commands.add_parser("run")
    command.add_argument("experiment", type=Path)
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    command = commands.add_parser("demo")
    command.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    from .control_contracts import load, save_new
    from .control_plane import ObservationBus, builtin_registry, experiment, observations_from_run, plan_graph, run_graph
    from .control_checks import compare, inspect_record
    try:
        if args.command in {"providers", "capabilities"}:
            result = _registry(args.catalog).catalog(getattr(args, "capability", None))
            _show(result, args.json)
        elif args.command == "inspect":
            if len(args.target) not in (1, 2):
                raise ValueError("Use net inspect FILE or net inspect KIND FILE")
            kind, target = args.target if len(args.target) == 2 else ("auto", args.target[0])
            if kind == "provider":
                catalog = _registry(args.catalog).catalog()
                if target not in catalog["providers"]:
                    raise ValueError("Provider is not in the explicit catalog")
                result = {**catalog, "providers": {target: catalog["providers"][target]},
                          "operations": {key: item for key, item in catalog["operations"].items() if item["provider"] == target}}
            else:
                result = inspect_path(Path(target), kind)
            _show(result, args.json)
        elif args.command == "compare":
            left, right = load(args.left), load(args.right)
            streams = []
            for value in (left, right):
                inspect_record(value)
                if value["schema"] == "ciw.thermal-observation-view.v1":
                    streams.append(value["stream"])
                elif value["schema"] == "ciw.observation-stream.v1":
                    streams.append(value)
                else:
                    raise ValueError("Comparison requires observation streams or supported native observation views")
            result = compare(streams[0]["observations"], streams[1]["observations"], atol=args.atol, rtol=args.rtol)
            if args.output:
                save_new(args.output, result)
            _show(result, args.json)
            return {"PASS": 0, "FAIL": 2, "INDETERMINATE": 3}[result["outcome"]["status"]]
        else:
            from .instruments import make_demo_run, validate_run
            from .session import Session
            registry = builtin_registry(bind=True)
            if args.command == "demo":
                run = make_demo_run()
                graph = experiment("oscillator-control-demo", model_id="analytic-damped-oscillator.v1", nodes=[
                    _node("statistics", "statistics.v1"), _node("spectrum", "spectrum.periodogram.v1", ["statistics"])])
            else:
                run, graph = load(args.run), load(args.experiment)
            validate_run(run)
            plan_graph(graph, registry)
            for node in graph["nodes"]:
                registry.operations.get(node["operation_id"])
            args.output_dir.mkdir(parents=True, exist_ok=False)
            session = Session(run, args.output_dir, operations=registry.operations)
            result = run_graph(session, graph, registry)
            session.save_workspace(args.output_dir / "workspace.json")
            save_new(args.output_dir / "experiment.json", graph)
            save_new(args.output_dir / "graph-run.json", result)
            if args.command == "demo":
                bus = ObservationBus(1024)
                for item in observations_from_run(run, channel="q", entity_id="oscillator",
                        model_id="analytic-damped-oscillator.v1", clock_id="seconds-since-model-start",
                        semantics="reference"):
                    bus.publish(item)
                save_new(args.output_dir / "observations.json", bus.snapshot())
            print(json.dumps({"status": result["status"], "output_dir": str(args.output_dir),
                              "session_id": session.session_id, "executions": len(session.executions)}))
            return 0 if result["status"] == "completed" else 2
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
