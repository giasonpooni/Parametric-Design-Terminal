"""First use retains real results, scoped checks, and failures without overwrites."""
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ciw import operator_start as start


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return json.loads(path.read_bytes())


def _hash(path):
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    output = tmp_path_factory.mktemp("first-use") / "instrument"
    report = start.run(output)
    assert report["status"] == "completed", report.get("failure")
    return output, report


def test_first_use_runs_reopens_and_checks_the_existing_core(completed):
    output, report = completed
    assert _read(output / "readiness.json") == report
    assert [(row["check"], row["status"]) for row in report["checks"]] == [
        ("core_preflight", "passed"), ("oscillator", "passed"),
        ("thermal", "passed"), ("atmosphere", "passed")]
    assert report["authority"] == start.AUTHORITY
    assert report["authority"]["external_provider_qualification"] == "not_performed"
    oscillator = report["workflows"]["oscillator"]
    assert oscillator["reopen"] == "retained_identities_preserved_without_reexecution"
    assert {row["operation_id"] for row in oscillator["operations"]} == {
        "statistics.v1", "spectrum.periodogram.v1"}
    assert all(row["verification_status"] == "not_verified" and row["verification_id"] is None
               for row in oscillator["operations"])
    assert oscillator["workspace_sha256"] == _hash(output / "oscillator/workspace.json")
    thermal = report["workflows"]["thermal"]
    for prefix in ("execution", "result"):
        assert thermal["original_" + prefix + "_id"] != thermal["replay_" + prefix + "_id"]
    assert thermal["source_bundle_id"] != thermal["replayed_bundle_id"]
    assert thermal["checks"]["replay-comparison"]["status"] == "PASS"
    # Dropout and posterior/prediction differences are expected diagnostics,
    # rather than a promise that all comparisons must pass.
    assert thermal["checks"]["measurement-comparison"]["status"] == "INDETERMINATE"
    assert thermal["checks"]["prediction-comparison"]["status"] == "FAIL"
    assert thermal["verification"]["outcome"] == "passed"
    assert thermal["verification"]["independent"] is False
    atmosphere = report["workflows"]["atmosphere"]
    assert atmosphere["status"] == "LOCAL"
    assert len(atmosphere["checks"]) == 17
    assert all(row["status"] == "PASS" for row in atmosphere["checks"])
    inspected = _read(output / "atmosphere/inspection.json")
    verified = _read(output / "atmosphere/verification-report.json")
    assert inspected["fresh_numerical_verification"] is False
    assert verified["fresh_numerical_verification"] is True
    assert verified["fresh_execution"] is False
    for key in ("execution_id", "result_id", "verification_execution_id", "verification_id"):
        assert inspected[key] == verified[key] == atmosphere[key]
    assert (output / "atmosphere/column.csv").is_file()


def test_receipt_hashes_exact_retained_artifacts_and_example_bytes(completed):
    output, report = completed
    assert files("ciw").joinpath("operator_examples", "thermal-source.json").read_bytes() == (
        ROOT / "examples/thermal-observations/source.json").read_bytes()
    expected = {path.relative_to(output).as_posix() for path in output.rglob("*")
                if path.is_file() and path.name != "readiness.json"}
    assert {row["path"] for row in report["artifacts"]} == expected
    for row in report["artifacts"]:
        path = output / row["path"]
        assert row["byte_count"] == path.stat().st_size
        assert row["sha256"] == _hash(path)
    assert report["workflows"]["thermal"]["source_sha256"] == _hash(output / "thermal/source.json")


def test_completed_output_is_create_only_and_cannot_be_reused(completed, capsys):
    output, _ = completed
    before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    assert start.main(["--output-dir", str(output)]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert before == {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}


def test_failure_preserves_completed_checks_and_does_not_run_later_workflows(tmp_path, monkeypatch):
    output = tmp_path / "failed"

    def fail_thermal(directory):
        directory.mkdir()
        (directory / "failure-context.txt").write_text("provider-free test failure")
        raise RuntimeError("injected thermal failure")

    monkeypatch.setattr(start, "_thermal", fail_thermal)
    monkeypatch.setattr(start, "_atmosphere", lambda _: pytest.fail("ran after failure"))
    report = start.run(output)
    assert report["status"] == "failed"
    assert [(row["check"], row["status"]) for row in report["checks"]] == [
        ("core_preflight", "passed"), ("oscillator", "passed"), ("thermal", "failed")]
    assert report["failure"] == {"error_type": "RuntimeError", "reason": "injected thermal failure"}
    assert set(report["workflows"]) == {"oscillator"}
    assert (output / "oscillator/workspace.json").is_file()
    assert not (output / "atmosphere").exists()
    assert _read(output / "readiness.json") == report
    assert any(row["path"] == "thermal/failure-context.txt" for row in report["artifacts"])


def test_blocked_preflight_retains_diagnostics_before_any_execution(tmp_path, monkeypatch, capsys):
    from ciw import doctor

    blocked = {"status": "blocked", "qualification": "not_performed",
               "checks": [{"check": "core_dependency:numpy", "status": "failed"}]}
    monkeypatch.setattr(doctor, "diagnose", lambda profile: blocked)
    monkeypatch.setattr(start, "_oscillator", lambda _: pytest.fail("executed with blocked setup"))
    output = tmp_path / "blocked"
    assert start.main(["--output-dir", str(output)]) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "failed"
    assert summary["receipt_sha256"] == _hash(output / "readiness.json")
    assert "net doctor --profile core" in summary["failure"]["reason"]
    assert _read(output / "preflight.json") == blocked
    assert not (output / "oscillator").exists()


def test_cold_start_with_uninstalled_dependencies_reports_setup_without_import_traceback(tmp_path):
    output = tmp_path / "cold"
    result = subprocess.run([sys.executable, "-S", "-m", "ciw.operator_start", "--output-dir", str(output)],
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                            capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert result.returncode == 2
    assert result.stderr == ""
    summary = json.loads(result.stdout)
    assert summary["status"] == "failed"
    assert "net doctor --profile core" in summary["failure"]["reason"]
    assert [row["check"] for row in summary["checks"]] == ["core_preflight"]
    assert _read(output / "readiness.json")["workflows"] == {}


def test_invalid_retained_oscillator_occurrence_cannot_claim_readiness(tmp_path, monkeypatch):
    from ciw import net

    original = net.main

    def tampered_demo(argv):
        code = original(argv)
        workspace = Path(argv[-1]) / "workspace.json"
        value = _read(workspace)
        value["executions"][0]["status"] = "refused"
        workspace.write_text(json.dumps(value))
        return code

    monkeypatch.setattr(net, "main", tampered_demo)
    monkeypatch.setattr(start, "_thermal", lambda _: pytest.fail("continued after invalid retained result"))
    report = start.run(tmp_path / "tampered")
    assert report["status"] == "failed"
    assert report["checks"][-1]["check"] == "oscillator"
    assert report["checks"][-1]["status"] == "failed"
    assert "completed operations" in report["failure"]["reason"]


def test_symlink_destination_does_not_modify_existing_output(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "kept.txt").write_text("operator data")
    link = tmp_path / "destination"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip("Directory symlinks are unavailable on this host: " + str(exc))
    with pytest.raises(FileExistsError):
        start.run(link)
    assert list(target.iterdir()) == [target / "kept.txt"]
    assert (target / "kept.txt").read_text() == "operator data"
