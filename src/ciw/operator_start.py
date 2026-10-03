"""Run and retain the existing bounded synthetic core workflows for first use.

This operator receipt records observed setup, execution, reopen and numerical
checks. It does not qualify external providers or perform state admission.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
import io
import json
import os
from pathlib import Path
import sys
import tempfile

AUTHORITY = {
    "scope": "bounded_synthetic_core_workflows",
    "external_provider_qualification": "not_performed",
    "physical_validation": "not_established",
    "state_admission": "not_performed",
    "hardware_actuation": "not_performed",
}


def _bytes_ref(raw: bytes) -> str:
    return "sha256:" + sha256(raw).hexdigest()


def _save_status(path: Path, value: dict) -> None:
    """Publish setup/receipt JSON without importing unavailable core dependencies."""
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with tempfile.TemporaryDirectory(prefix=".net-start-", dir=path.parent) as temporary:
        staged = Path(temporary) / "status.json"
        with staged.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)


def _checked(report: dict, name: str, action) -> dict:
    row = {"check": name, "status": "running"}
    report["checks"].append(row)
    try:
        value = action()
    except Exception as exc:
        row.update(status="failed", reason=str(exc), error_type=type(exc).__name__)
        raise
    row.update(status="passed", observed=value)
    return value


def _core_preflight(output: Path) -> dict:
    from .doctor import diagnose

    report = diagnose("core")
    _save_status(output / "preflight.json", report)
    if report["status"] != "preflight_passed":
        failures = [row["check"] for row in report["checks"] if row["status"] != "passed"]
        raise ValueError("Core preflight is blocked: " + ", ".join(failures) +
                         "; run net doctor --profile core to inspect setup")
    return {"status": report["status"], "qualification": report["qualification"],
            "checks": [row["check"] for row in report["checks"]]}


def _oscillator(output: Path) -> dict:
    from . import net
    from .control_contracts import load
    from .session import Session

    captured = io.StringIO()
    with redirect_stdout(captured):
        code = net.main(["demo", "--output-dir", str(output)])
    if code != 0:
        raise ValueError("The existing oscillator demo failed; inspect oscillator artifacts")
    summary = json.loads(captured.getvalue())
    graph = load(output / "graph-run.json")
    workspace_path = output / "workspace.json"
    workspace = load(workspace_path)
    if (summary["status"] != "completed" or graph["status"] != "completed"
            or len(workspace["results"]) != 2 or len(workspace["executions"]) != 2
            or any(row["status"] != "completed" for row in workspace["executions"])
            or any(row["status"] != "completed" for row in graph["nodes"].values())):
        raise ValueError("The oscillator demo did not retain both completed operations")
    original = workspace_path.read_bytes()
    with tempfile.TemporaryDirectory(prefix="net-start-reopen-") as temporary:
        restored = Session.from_workspace(workspace_path, Path(temporary) / "checked")
        if (restored.run["evidence_id"] != workspace["run"]["evidence_id"]
                or restored.results != {row["result_id"]: row for row in workspace["results"]}
                or restored.executions != {row["execution_id"]: row for row in workspace["executions"]}):
            raise ValueError("Oscillator reopen changed retained evidence or occurrence identities")
    if workspace_path.read_bytes() != original:
        raise ValueError("Oscillator reopen changed the source workspace")
    return {"status": "completed", "session_id": graph["session_id"],
            "source_evidence_id": workspace["run"]["evidence_id"],
            "operations": [{"operation_id": row["operation_id"],
                            "execution_id": row["execution_id"], "result_id": row["result_id"],
                            "verification_status": row["verification_status"],
                            "verification_id": row["verification_id"]}
                           for row in workspace["results"]],
            "reopen": "retained_identities_preserved_without_reexecution",
            "workspace_sha256": _bytes_ref(original)}


def _thermal(output: Path) -> dict:
    # This is the existing thermal-observations example, packaged for installed
    # first use. No repository, Julia runtime or newly implemented math is bound.
    from . import scientific
    from .control_contracts import save_new
    from .control_checks import compare
    from .instruments import make_demo_run
    from .scientific_observations import export_observations, match_workspace
    from .session import Session

    raw = files("ciw").joinpath("operator_examples", "thermal-source.json").read_bytes()
    scientific.source_payload("thermal-observer", raw, "declared synthetic thermal case")
    output.mkdir(parents=True, exist_ok=False)
    with (output / "source.json").open("xb") as stream:
        stream.write(raw)
    session = Session(make_demo_run(), output / "original")
    try:
        selected = scientific.execute(session, "thermal-observer", raw,
                                      label="declared synthetic thermal case")
    finally:
        workspace = session.save_workspace(output / "original/workspace.json")
    if (selected["validation"] != "content_consistent"
            or selected["retained_verification_outcome"] != "passed"
            or not selected["result_ids"] or not selected["execution_ids"]):
        raise ValueError("The existing thermal reference did not complete")
    digest = _bytes_ref(workspace.read_bytes())
    views = {}
    for stage in ("predicted", "posterior", "measurement"):
        value = export_observations(workspace, expected_sha256=digest,
            bundle_id=selected["bundle_id"], stage=stage, entity_id="demo/core-shell")
        save_new(output / (stage + ".json"), value)
        match_workspace(value, workspace)
        views[stage] = value
    with scientific.open_workspace(workspace, output / "replay", expected_sha256=digest) as restored:
        replay = scientific.replay(restored, selected["bundle_id"], repositories={})
        if (replay["bundle"]["validation"] != "content_consistent"
                or replay["bundle"]["retained_verification_outcome"] != "passed"
                or replay["replay_receipt"]["numerical_match"] is not True):
            raise ValueError("The existing thermal replay did not complete")
        original_bundle = restored.workbench.get_bundle(selected["bundle_id"])
        replay_bundle = restored.workbench.get_bundle(replay["bundle"]["bundle_id"])
    replay_path = output / "replay/workspace.json"
    replay_digest = _bytes_ref(replay_path.read_bytes())
    replayed = export_observations(replay_path, expected_sha256=replay_digest,
        bundle_id=replay["bundle"]["bundle_id"], stage="posterior", entity_id="demo/core-shell")
    save_new(output / "replayed-posterior.json", replayed)
    match_workspace(replayed, replay_path)
    original_step, fresh_step = views["posterior"]["native_step"], replayed["native_step"]
    if (original_step["execution_id"] == fresh_step["execution_id"]
            or original_step["result_id"] == fresh_step["result_id"]
            or selected["bundle_id"] == replay["bundle"]["bundle_id"]):
        raise ValueError("Replay must retain a fresh execution and result occurrence")
    checks = {}
    for label, left, right, atol in (
        ("replay-comparison", views["posterior"], replayed, 0.),
        ("measurement-comparison", views["posterior"], views["measurement"], 1.),
        ("prediction-comparison", views["posterior"], views["predicted"], 0.),
    ):
        result = compare(left["stream"]["observations"], right["stream"]["observations"], atol=atol)
        save_new(output / (label + ".json"), result)
        checks[label] = result["outcome"]
    if checks["replay-comparison"]["status"] != "PASS":
        raise ValueError("Existing same-runtime replay did not match its original posterior")
    verification = original_bundle["verification"]
    replay_verification = replay_bundle["verification"]
    if verification["outcome"] != "passed" or replay_verification["outcome"] != "passed":
        raise ValueError("The retained thermal same-runtime reproduction did not pass")
    report = {"status": "completed", "scope": "synthetic_python_reference_and_retained_projection_only",
        "source_sha256": _bytes_ref(raw), "source_evidence_id": views["posterior"]["binding"]["source_evidence_id"],
        "workspace_sha256": digest, "replay_workspace_sha256": replay_digest,
        "source_bundle_id": selected["bundle_id"], "replayed_bundle_id": replay["bundle"]["bundle_id"],
        "original_execution_id": original_step["execution_id"], "replay_execution_id": fresh_step["execution_id"],
        "original_result_id": original_step["result_id"], "replay_result_id": fresh_step["result_id"],
        "verification": {"verification_id": verification["verification_id"], "outcome": verification["outcome"],
                         "method": verification["method"], "independent": verification["independent"]},
        "replay_verification_id": replay_verification["verification_id"],
        "replay_receipt_id": replay_bundle["replay_receipts"][0]["replay_id"],
        "samples": len(views["posterior"]["stream"]["observations"]), "checks": checks,
        "physical_validation": "not_established", "state_admission": "not_performed"}
    save_new(output / "demo-report.json", report)
    return report


def _atmosphere(output: Path) -> dict:
    from . import atmosphere_workflow as workflow
    from .atmosphere_contract import example_request
    from .control_contracts import load, save_new

    output.mkdir(parents=True, exist_ok=False)
    request = example_request()
    save_new(output / "request.json", request)
    directory = output / "column"
    original = workflow.run(request, directory)
    save_new(output / "run-report.json", original)
    inspected = workflow.inspect(directory)
    save_new(output / "inspection.json", inspected)
    if original != inspected or inspected["status"] != "LOCAL":
        raise ValueError("The atmosphere example did not retain a LOCAL reference result")
    verified = workflow.verify_retained(directory)
    save_new(output / "verification-report.json", verified)
    if (verified["status"] != "LOCAL" or not verified["fresh_numerical_verification"]
            or verified["fresh_execution"] or not verified["checks"]
            or any(row["status"] != "PASS" for row in verified["checks"])
            or any(verified[key] != inspected[key] for key in
                   ("evidence_id", "execution_id", "result_id", "verification_execution_id", "verification_id"))):
        raise ValueError("Independent atmosphere checks failed or changed retained occurrence identities")
    exported = workflow.export_csv(directory, output / "column.csv")
    save_new(output / "export-report.json", exported)
    if (exported["status"] != "exported" or exported["source_result_id"] != inspected["result_id"]
            or exported["verification_id"] != inspected["verification_id"]):
        raise ValueError("The atmosphere CSV did not bind the selected retained occurrence")
    workspace = load(directory / "workspace.json")
    verification_result = next(row for row in workspace["results"]
                               if row["operation_id"] == workflow.VERIFY)
    return {"status": inspected["status"], "source_evidence_id": inspected["evidence_id"],
            "execution_id": inspected["execution_id"], "result_id": inspected["result_id"],
            "verification_execution_id": inspected["verification_execution_id"],
            "verification_result_id": verification_result["result_id"],
            "verification_id": inspected["verification_id"],
            "verification_method": "independent_dry_hydrostatic_numerical_audit",
            "recheck": "fresh_numerical_diagnostics_preserve_retained_verification_identity",
            "recomputed_report_digest": verified["recomputed_report_digest"],
            "checks": verified["checks"], "authority": verified["authority"]}


def _artifacts(output: Path) -> list[dict]:
    return [{"path": path.relative_to(output).as_posix(), "byte_count": path.stat().st_size,
             "sha256": _bytes_ref(path.read_bytes())}
            for path in sorted(output.rglob("*")) if path.is_file() and path.name != "readiness.json"]


def run(output: Path) -> dict:
    """Create a first-use directory; preserve completed work and a failed receipt."""
    output = Path(output).expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "ciw.operator-readiness.v1", "status": "failed",
              "output_dir": str(output), "authority": dict(AUTHORITY), "checks": [],
              "workflows": {}, "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        _checked(report, "core_preflight", lambda: _core_preflight(output))
        for name, action in (("oscillator", _oscillator), ("thermal", _thermal), ("atmosphere", _atmosphere)):
            report["workflows"][name] = _checked(report, name, lambda action=action, name=name: action(output / name))
        report["status"] = "completed"
    except Exception as exc:
        report["failure"] = {"error_type": type(exc).__name__, "reason": str(exc)}
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["artifacts"] = _artifacts(output)
    _save_status(output / "readiness.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New directory for retained first-use outputs and readiness.json")
    args = parser.parse_args(argv)
    try:
        report = run(args.output_dir)
        receipt = Path(report["output_dir"]) / "readiness.json"
        summary = {"status": report["status"], "scope": AUTHORITY["scope"], "receipt": str(receipt),
                   "receipt_sha256": _bytes_ref(receipt.read_bytes()),
                   "checks": [{"check": row["check"], "status": row["status"]} for row in report["checks"]]}
        if "failure" in report:
            summary["failure"] = report["failure"]
        print(json.dumps(summary))
        return 0 if report["status"] == "completed" else 2
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
