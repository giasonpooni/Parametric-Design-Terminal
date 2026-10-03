"""Core diagnostics remain available before scientific dependencies can import."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ciw import doctor, operator_doctor


def test_core_reports_missing_dependency_without_loading_or_installing_it(monkeypatch, capsys):
    def distribution(name):
        if name == "computational-instrumentation-workbench":
            return SimpleNamespace(version="fixture", metadata={"Requires-Python": ">=3.11"},
                                   requires=["numpy==2.4.3", "websockets==16.0"])
        raise doctor.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(doctor.metadata, "distribution", distribution)
    assert operator_doctor.main(["--profile", "core"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked"
    assert report["read_only"] is True and report["qualification"] == "not_performed"
    assert report["classifications"] == ["dependency_unavailable"]
    missing = {row["check"] for row in report["checks"] if row["status"] == "failed"}
    assert missing == {"core_dependency:numpy", "core_dependency:websockets"}


def test_operator_doctor_retains_existing_cli_report_and_status(capsys):
    from ciw.cli import main as ciw_main

    code = ciw_main(["doctor", "--profile", "core"])
    old = json.loads(capsys.readouterr().out)
    assert operator_doctor.main(["--profile", "core"]) == code
    assert json.loads(capsys.readouterr().out) == old


def test_existing_profile_arguments_are_validated_by_doctor(tmp_path, capsys):
    assert operator_doctor.main(["--profile", "core", "--binding", str(tmp_path / "binding.json")]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["profile"] == "core" and report["classifications"] == ["setup_failure"]


def test_unknown_profile_retains_nonzero_argument_refusal():
    with pytest.raises(SystemExit) as refused:
        operator_doctor.main(["--profile", "imaginary-provider"])
    assert refused.value.code == 2


def _cold_python(tmp_path, *arguments):
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    return subprocess.run([sys.executable, "-S", *arguments], env=environment, cwd=tmp_path,
                          text=True, capture_output=True, timeout=10)


def test_core_doctor_without_site_packages_emits_classified_report(tmp_path):
    result = _cold_python(tmp_path, "-m", "ciw.operator_doctor", "--profile", "core")
    assert result.returncode == 2 and not result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "blocked" and report["classifications"] == ["dependency_unavailable"]
    assert report["checks"][0]["check"] == "ciw_distribution"
    assert not list(tmp_path.iterdir())


def test_help_without_site_packages_lists_existing_profile_and_binding_flags(tmp_path):
    result = _cold_python(tmp_path, "-m", "ciw.operator_doctor", "--help")
    assert result.returncode == 0 and not result.stderr
    for profile in doctor.PROFILES:
        assert profile in result.stdout
    for flag in ("--profile", "--stack-root", "--engine", "--binding"):
        assert flag in result.stdout


def test_core_entrypoint_never_imports_scientific_cli_numpy_or_websockets(tmp_path):
    code = (
        "import sys; from ciw.operator_doctor import main; result=main(['--profile','core']); "
        "assert 'ciw.cli' not in sys.modules; assert 'numpy' not in sys.modules; "
        "assert 'websockets' not in sys.modules; raise SystemExit(result)"
    )
    result = _cold_python(tmp_path, "-c", code)
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)["qualification"] == "not_performed"


def test_net_doctor_is_available_without_site_packages(tmp_path):
    result = _cold_python(tmp_path, "-m", "ciw.net", "doctor", "--profile", "core")
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)["classifications"] == ["dependency_unavailable"]
    help_result = _cold_python(tmp_path, "-m", "ciw.net", "doctor", "--help")
    assert help_result.returncode == 0 and "--binding" in help_result.stdout
    assert not help_result.stderr
