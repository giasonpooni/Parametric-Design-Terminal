"""Discovery cannot bind, launch or claim to qualify optional providers."""
import json
import subprocess

from ciw import net, operator_catalog
from ciw.workbench import OPERATIONS, Workbench


def test_catalog_is_complete_without_binding_or_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Catalog must not launch or bind a provider")
    monkeypatch.setattr(Workbench, "bind_workflow", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    value = operator_catalog.catalog()
    assert value["read_only"] is True and value["authorizes_execution"] is False
    assert {row["workflow"] for row in value["workflows"]} == set(OPERATIONS)
    assert {row["command"] for row in value["commands"]} >= {
        "net start", "net workbench", "net provision", "net atmosphere", "net science", "net inspect", "net polymer"}
    assert all(row["qualification"] == "not_performed_by_catalog" for row in value["workflows"])
    thermal = next(row for row in value["workflows"] if row["workflow"] == "thermal-observer")
    assert thermal["available"] is True and thermal["providers"] == {}
    assert thermal["scientific_cli"] is True
    calibrated = next(row for row in value["workflows"] if row["workflow"] == "calibrated-observable")
    assert calibrated["available"] is False and len(calibrated["providers"]) == 8
    assert all(len(row["revision"]) == 40 for row in calibrated["providers"].values())


def test_cli_catalog_and_existing_nested_help(capsys):
    assert net.main(["catalog", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "ciw.operator-catalog.v1"
    import pytest
    for command in (["--help"], ["science", "--help"], ["foundry", "childhood", "--help"],
                    ["polymer", "--help"], ["doctor", "--help"], ["legibility", "--help"], ["workbench", "--help"]):
        with pytest.raises(SystemExit) as exit_:
            net.main(command)
        assert exit_.value.code == 0
