"""Local startup keeps the existing persistence and provider authority boundaries."""
from pathlib import Path

import pytest

from ciw import cli, doctor, operator_workbench
from ciw.instruments import make_demo_run
from ciw.session import Session, write_json


@pytest.fixture
def started(monkeypatch):
    sessions = []

    async def capture(session, port=8765, bind="127.0.0.1", **kwargs):
        sessions.append((session, port, bind, kwargs))

    monkeypatch.setattr(cli, "run_server", capture)
    return sessions


def test_default_start_is_loopback_and_prints_live_discovery_commands(tmp_path, monkeypatch, started, capsys):
    monkeypatch.chdir(tmp_path)
    assert operator_workbench.main([]) == 0
    session, port, bind, _ = started[0]
    assert (port, bind, session.output_dir) == (8765, "127.0.0.1", Path(".ciw"))
    assert session.run["instrument"] == "analytic-damped-oscillator.v1"
    assert not session.executions and not session.results
    operations = session.handle({"protocol_version": 1, "request_id": "discover",
                                 "type": "operation.list", "payload": {}})
    assert operations["type"] == "response"
    rows = {row["operation_id"]: row for row in operations["payload"]["operations"]}
    for optional in ("ciw.calibrated-observable.v1", "ciw.identified-design.v1",
                     "ciw.geometric-circle.v1"):
        assert rows[optional]["available"] is False
    text = capsys.readouterr().out
    for command in ("health", "send session.get", "send operation.list",
                    "send result.list", "send workspace.save"):
        assert f"ciw {command} --url ws://127.0.0.1:8765" in text
    assert "scientific qualification not performed" in text


def test_default_resume_preserves_retained_execution_and_result_identities(tmp_path, started):
    output = tmp_path / "investigation"
    original = Session(make_demo_run(), output)
    result = original.handle({"protocol_version": 1, "request_id": "stats",
                              "type": "operation.execute",
                              "payload": {"operation_id": "statistics.v1", "parameters": {}}})
    assert result["type"] == "response"
    assert len(original.executions) == 1 and len(original.results) == 1
    original.save_workspace(output / "workspace.json")
    before = (output / "workspace.json").read_bytes()
    assert operator_workbench.main(["--output-dir", str(output), "--port", "8923"]) == 0
    restored, port, bind, _ = started[0]
    assert (port, bind) == (8923, "127.0.0.1")
    assert restored.run == original.run
    assert restored.executions == original.executions
    assert restored.results == original.results
    assert (output / "workspace.json").read_bytes() == before


@pytest.mark.parametrize("damaged", ["{", '{"workspace_version":999}', '{"workspace_version":4,"workspace_version":3}'])
def test_damaged_workspace_refuses_without_starting_fresh_evidence(tmp_path, started, capsys, damaged):
    output = tmp_path / "retained"
    output.mkdir()
    path = output / "workspace.json"
    path.write_text(damaged, encoding="utf-8")
    assert operator_workbench.main(["--output-dir", str(output)]) == 2
    assert not started
    assert path.read_text(encoding="utf-8") == damaged
    assert list(output.iterdir()) == [path]
    assert "ciw:" in capsys.readouterr().err


def test_explicit_recording_uses_recording_instead_of_saved_workspace(tmp_path, started):
    output = tmp_path / "target"
    output.mkdir()
    saved = output / "workspace.json"
    saved.write_text("damaged saved state", encoding="utf-8")
    source = tmp_path / "source.json"
    run = make_demo_run()
    run["run_id"] = "run-explicit-operator-evidence"
    write_json(source, run)
    assert operator_workbench.main(["--recording", str(source), "--output-dir", str(output)]) == 0
    assert started[0][0].run["run_id"] == run["run_id"]
    assert saved.read_text(encoding="utf-8") == "damaged saved state"


def test_explicit_workspace_reopens_into_chosen_directory(tmp_path, started):
    original = Session(make_demo_run(), tmp_path / "original")
    saved = original.save_workspace(tmp_path / "saved.json")
    output = tmp_path / "reopened"
    assert operator_workbench.main(["--workspace", str(saved), "--output-dir", str(output),
                                   "--bind", "0.0.0.0", "--port", "8766"]) == 0
    session, port, bind, _ = started[0]
    assert (port, bind, session.output_dir) == (8766, "0.0.0.0", output)
    assert session.run == original.run


@pytest.mark.parametrize("arguments", [
    ["--recording", "source.json", "--workspace", "workspace.json"],
    ["--workspace", "workspace.json", "--resume"],
    ["--identified-stack-root", "stack", "--calibrated-stack-root", "stack"],
    ["--bind", "192.0.2.1"],
])
def test_existing_parser_rejects_conflicting_or_remote_bind_arguments(arguments, started):
    with pytest.raises(SystemExit) as refused:
        operator_workbench.main(arguments)
    assert refused.value.code == 2
    assert not started


@pytest.mark.parametrize("port", ["0", "65536"])
def test_invalid_ports_retain_existing_serve_refusal(tmp_path, port, started, capsys):
    output = tmp_path / "not-created"
    assert operator_workbench.main(["--output-dir", str(output), "--port", port]) == 2
    assert not started and not output.exists()
    assert "port must be between 1 and 65535" in capsys.readouterr().err


def test_explicit_provider_binding_still_passes_existing_checkout_validation(tmp_path, started, capsys):
    provider = tmp_path / "not-a-provider"
    provider.mkdir()
    assert operator_workbench.main(["--output-dir", str(tmp_path / "session"),
                                   "--geometry-repo", str(provider)]) == 2
    assert not started
    assert "ciw:" in capsys.readouterr().err


def test_core_dependency_failure_is_actionable_and_creates_no_workspace(tmp_path, monkeypatch, started, capsys):
    installed = doctor.metadata.distribution

    def missing(name):
        if name == "numpy":
            raise doctor.metadata.PackageNotFoundError(name)
        return installed(name)

    monkeypatch.setattr(doctor.metadata, "distribution", missing)
    output = tmp_path / "not-created"
    assert operator_workbench.main(["--output-dir", str(output)]) == 2
    assert not started and not output.exists()
    error = capsys.readouterr().err
    assert "core_dependency:numpy" in error
    assert "python -m pip install -e ." in error
    assert "ciw doctor --profile core" in error
