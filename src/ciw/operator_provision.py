"""Explicit, local-only provisioning of existing scientific provider pins.

Only installed NET declarations select provider code. The operator-selected
monorepo supplies retained Git objects, never Python helpers or working files.
Each output provider is a standalone detached checkout; no dependency installer,
compiler, network fetch, or scientific provider is invoked here.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
from importlib import resources
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

from .provider_checkouts import validate_checkout, validate_tracked_checkout
from .control_contracts import json_tree, save_new
from .workbench import OPERATIONS, UPSTREAM_KINDS, _workflow

PLAN_SCHEMA = "ciw.operator-provision-plan.v1"
BINDINGS_SCHEMA = "ciw.operator-bindings.v1"
REPORT_SCHEMA = "ciw.operator-provision-report.v1"
MAX_BYTES = 65536
_SHA = re.compile(r"[0-9a-f]{40}")
_ROLE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_EXTERNAL_TYPES = {"engine": "executable_file", "prover": "executable_file", "guest": "artifact_file",
                   "julia": "executable_file", "julia_runtime": "runtime_directory", "runtime": "binding_json"}


def _json(raw: bytes):
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Operator JSON requires 1..65536 exact bytes")
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate operator JSON field: " + key)
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("Nonfinite operator JSON value: " + value)
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    json_tree(value)
    return value


def _read(path):
    with Path(path).open("rb") as stream:
        return _json(stream.read(MAX_BYTES + 1))


def _write(path, value):
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("Operator report exceeds its 64 KiB byte budget")
    save_new(Path(path), value)


def _paths(value):
    if type(value) is not dict or len(value) > 32:
        raise ValueError("Bindings require a bounded role-to-absolute-path object")
    result = {}
    for role, raw in value.items():
        if type(role) is not str or not _ROLE.fullmatch(role):
            raise ValueError("Malformed operator binding role")
        if type(raw) is not str or not raw.strip() or len(raw) > 4096 or "\0" in raw or not Path(raw).is_absolute():
            raise ValueError("Each operator binding requires an explicit absolute path")
        result[role] = Path(raw)
    return result


def _git(root, *arguments):
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"}
    for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_TEMPLATE_DIR"):
        environment.pop(name, None)
    command = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
               "-c", "core.hooksPath=/dev/null", "-C", str(root)]
    if arguments and arguments[0] in {"clone", "checkout"}:
        # Attributes name filters; configured smudge/process commands can fetch
        # or execute tools. Materialization must retain original blob bytes.
        configured = subprocess.run([*command, "config", "--name-only", "--get-regexp", r"^filter\..*\.(smudge|process|required)$"],
            env=environment, capture_output=True, timeout=60)
        if configured.returncode not in {0, 1} or len(configured.stdout) > MAX_BYTES:
            raise ValueError("Cannot inspect local Git materialization filter declarations")
        drivers = set()
        for key in configured.stdout.decode("utf-8").splitlines():
            matched = re.fullmatch(r"filter\.(.+)\.(?:smudge|process|required)", key)
            if not matched or len(matched[1]) > 512:
                raise ValueError("Malformed Git filter declaration")
            drivers.add(matched[1])
        for driver in sorted(drivers):
            command.extend(["-c", f"filter.{driver}.smudge=", "-c", f"filter.{driver}.process=",
                            "-c", f"filter.{driver}.required=false"])
    return subprocess.run([*command, *arguments], env=environment,
        check=True, capture_output=True, timeout=60).stdout


def workflow_requirements(kind: str) -> dict:
    """Project installed workflow authorities, without probing or executing them."""
    if kind not in OPERATIONS:
        raise ValueError("Select an existing Workbench workflow")
    workflow = _workflow(kind)
    roles = set(workflow.ROLES)
    if kind == "calibrated-observable":
        pins = _json(resources.files("ciw").joinpath("calibrated-observable-runtimes.json").read_bytes())
    elif kind == "identified-design":
        from .identified_design import _pins
        pins = _pins()
    elif kind == "telemetry":
        pins = _json(resources.files("ciw").joinpath("telemetry-runtimes.json").read_bytes())
    elif kind in {"calibrated-window", "acquired-calibrated-window"}:
        from .calibrated_window import _pins
        pins = _pins()
    elif kind == "measurement-chain":
        from .measurement_chain import PINS
        pins = PINS
    elif kind == "schematic-companions":
        from .schematic_companions import PINS
        pins = PINS
    elif kind == "residual-monitor":
        from .residual_monitor import PINS
        pins = PINS
    elif kind == "variational-free-energy":
        from .free_energy_native import PINS
        pins = PINS
    elif kind == "instrument-exchange":
        from .exchange_adapter import PIN
        pins = {"set": PIN}
    elif kind == "acquired-dataset":
        from .acquired_dataset import PPDA_REVISION, SOURCE_TREES
        pins = {"ppda": {"revision": PPDA_REVISION, "source_tree": SOURCE_TREES[PPDA_REVISION]}}
    elif hasattr(workflow, "pin"):
        pins = {workflow.role: workflow.pin}
    else:
        pins = {}
    if not set(pins) <= roles or roles - set(pins) - set(_EXTERNAL_TYPES):
        raise ValueError("Workflow has an unsupported provisioning binding authority")
    for role, pin in pins.items():
        if type(pin) is not dict or not _SHA.fullmatch(pin.get("revision", "")):
            raise ValueError("Workflow provider must retain a full original commit identity")
    external = {role: {"binding_type": _EXTERNAL_TYPES[role], "identity": "operator_supplied_not_attested"}
                for role in sorted(roles - set(pins))}
    return {"workflow": kind, "operation_id": OPERATIONS[kind], "providers": deepcopy(pins),
            "external_bindings": external, "requires_upstream_bundle": kind in UPSTREAM_KINDS,
            "qualification": "not_performed", "authorizes_execution": False}


def catalog() -> dict:
    return {"schema": PLAN_SCHEMA, "read_only": True,
            "workflows": [workflow_requirements(kind) for kind in sorted(OPERATIONS)],
            "qualification": "not_performed", "authorizes_execution": False}


def _manifest(root, revision):
    value = _json(_git(root, "show", revision + ":instruments/manifest.json"))
    if type(value) is not dict or set(value) != {"schema", "modules"} or value["schema"] != "notations.monorepo-imports.v1":
        raise ValueError("Monorepo requires its committed public module manifest")
    if type(value["modules"]) is not list or not 1 <= len(value["modules"]) <= 32:
        raise ValueError("Module manifest requires a bounded module list")
    modules = {}
    for row in value["modules"]:
        if type(row) is not dict or type(row.get("role")) is not str or not _ROLE.fullmatch(row["role"]) or row["role"] in modules:
            raise ValueError("Module manifest requires unique provider roles")
        modules[row["role"]] = row
    return modules


def _retained(root, source, module, role, pin):
    prefix = module.get("path")
    if (module.get("visibility") != "public" or type(module.get("license")) is not str or not module["license"]
            or type(prefix) is not str or not prefix.startswith("instruments/")
            or any(part in {"", ".", ".."} for part in prefix.split("/"))):
        raise ValueError("Selected provider must have a preserved public import/licence boundary")
    imported, tree = module.get("import_revision"), module.get("import_tree")
    histories = module.get("additional_history_roots", [])
    if (not _SHA.fullmatch(imported or "") or not _SHA.fullmatch(tree or "") or type(histories) is not list
            or len(histories) > 16 or any(type(item) is not str or not _SHA.fullmatch(item) for item in histories)):
        raise ValueError("Selected provider history requires full source identities")
    for history in [imported, *histories]:
        _git(root, "merge-base", "--is-ancestor", history, source["revision"])
    if (_git(root, "rev-parse", imported + "^{tree}").decode().strip() != tree
            or _git(root, "rev-parse", source["revision"] + ":" + prefix).decode().strip() != tree):
        raise ValueError("Selected import differs from its retained original source tree")
    for history in [imported, *histories]:
        try:
            _git(root, "merge-base", "--is-ancestor", pin["revision"], history)
            break
        except subprocess.CalledProcessError:
            continue
    else:
        raise ValueError("Required workflow pin is absent from the selected public provider history: " + role)
    actual_tree = _git(root, "rev-parse", pin["revision"] + "^{tree}").decode().strip()
    if pin.get("source_tree", actual_tree) != actual_tree:
        raise ValueError("Original workflow pin has a different source tree: " + role)
    result = {"revision": pin["revision"], "source_tree": actual_tree, "license": module["license"],
              "repository": module.get("repository"), "module": pin.get("module"),
              "source_root": pin.get("source_root"), "dependencies": None, "requires_python": None,
              "dependency_identity": "declared_in_original_source_not_installed_or_verified"}
    try:
        raw = _git(root, "show", pin["revision"] + ":pyproject.toml")
    except subprocess.CalledProcessError:
        result["dependency_identity"] = "no_pyproject_declaration_at_original_pin"
    else:
        if len(raw) > MAX_BYTES:
            raise ValueError("Provider package declaration exceeds byte budget")
        project = tomllib.loads(raw.decode("utf-8")).get("project", {})
        result["dependencies"] = project.get("dependencies", [])
        result["requires_python"] = project.get("requires-python")
        result["build_system"] = tomllib.loads(raw.decode("utf-8")).get("build-system")
    return result


def _external(role, path, declaration):
    path = Path(path).resolve(strict=True)
    kind = declaration["binding_type"]
    if kind == "runtime_directory":
        if not path.is_dir():
            raise ValueError(role + " requires a runtime directory")
        return {"path": str(path), "binding_type": kind, "identity": "contents_not_attested"}
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(role + " requires a nonempty regular file")
    if kind == "binding_json":
        if type(_read(path)) is not dict:
            raise ValueError(role + " requires a bounded runtime binding object")
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {"path": str(path), "binding_type": kind, "sha256": "sha256:" + digest.hexdigest(),
            "identity": "reported_only_not_loadability_or_source_attestation"}


def plan(monorepo: Path, workflow: str, *, bindings: dict | None = None) -> dict:
    """Read committed source and declared local prerequisites; never provision."""
    requirements = workflow_requirements(workflow)
    explicit = _paths({} if bindings is None else {key: str(value) for key, value in bindings.items()})
    if not set(explicit) <= set(requirements["external_bindings"]):
        raise ValueError("Explicit prerequisites must name external file/directory roles only")
    root = Path(monorepo).resolve(strict=True)
    revision = _git(root, "rev-parse", "HEAD").decode().strip()
    validate_tracked_checkout(root, revision)
    source = {"root": str(root), "revision": revision,
              "source_tree": _git(root, "rev-parse", "HEAD^{tree}").decode().strip()}
    modules = _manifest(root, revision)
    result = {"schema": PLAN_SCHEMA, "workflow": workflow, "operation_id": requirements["operation_id"],
              "source": source, "providers": {}, "external_bindings": {}, "blockers": [],
              "requires_upstream_bundle": requirements["requires_upstream_bundle"],
              "source_preflight": "tracked_index_types_modes_and_working_bytes",
              "full_monorepo_import_audit": "not_performed", "qualification": "not_performed",
              "authorizes_execution": False, "read_only": True}
    for role, pin in sorted(requirements["providers"].items()):
        if role not in modules:
            result["blockers"].append({"role": role, "revision": pin["revision"], "reason": "provider_history_not_retained",
                "prerequisite": "Use an explicitly authorized exact provider checkout with the existing --binding CLI; no source is fetched here."})
            continue
        try:
            result["providers"][role] = _retained(root, source, modules[role], role, pin)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result["blockers"].append({"role": role, "revision": pin["revision"], "reason": str(exc)})
    for role, declaration in requirements["external_bindings"].items():
        if role not in explicit:
            result["blockers"].append({"role": role, "reason": "explicit_external_binding_required",
                "binding_type": declaration["binding_type"], "prerequisite": "Supply --binding " + role + "=/absolute/approved/path; provider tools remain separately qualified."})
        else:
            result["external_bindings"][role] = _external(role, explicit[role], declaration)
    if _git(root, "rev-parse", "HEAD").decode().strip() != revision:
        raise ValueError("Monorepo source revision changed during operator preflight")
    result["status"] = "blocked" if result["blockers"] else "ready_to_provision"
    return result


class ProvisionFailure(ValueError):
    def __init__(self, report):
        super().__init__(report.get("reason", "Provider provisioning is blocked"))
        self.report = report


def provision(monorepo: Path, workflow: str, output_dir: Path, *, bindings: dict | None = None) -> dict:
    """Create separate exact provider roots, retaining every partial failure."""
    selected = plan(monorepo, workflow, bindings=bindings)
    if selected["status"] != "ready_to_provision":
        raise ProvisionFailure(selected)
    requested = Path(output_dir).absolute()
    if any(component.is_symlink() for component in [requested, *requested.parents]):
        raise ValueError("Provision output must not traverse a symlink")
    output = Path(os.path.abspath(output_dir))
    if output.resolve() != output:
        raise ValueError("Provision output must not traverse a symlink")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": REPORT_SCHEMA, "workflow": workflow, "plan": selected, "status": "failed",
              "providers": {}, "qualification": "not_performed", "authorizes_execution": False}
    paths = {role: row["path"] for role, row in selected["external_bindings"].items()}
    root = Path(selected["source"]["root"])
    try:
        for role, row in selected["providers"].items():
            path = output / role
            report["providers"][role] = {"path": str(path), "revision": row["revision"], "status": "creating"}
            _git(root, "clone", "--quiet", "--shared", "--no-checkout", "--config", "core.autocrlf=false",
                 "--config", "core.hooksPath=/dev/null", "--config", "core.longpaths=true", str(root), str(path))
            # --shared alternates preserve local commit objects without copying
            # the whole monorepo. No remote URL is used or fetched.
            _git(path, "checkout", "--quiet", "--no-recurse-submodules", "--detach", row["revision"])
            validate_checkout(path, row["revision"])
            if _git(path, "rev-parse", "HEAD^{tree}").decode().strip() != row["source_tree"]:
                raise ValueError("Created provider source tree differs from selected workflow")
            report["providers"][role]["status"] = "source_preflight_passed"
            paths[role] = str(path)
        validate_tracked_checkout(root, selected["source"]["revision"])
        record = {"schema": BINDINGS_SCHEMA, "workflow": workflow, "bindings": paths,
                  "source": selected["source"], "providers": selected["providers"],
                  "qualification": "not_performed", "authorizes_execution": False}
        _write(output / "bindings.json", record)
        report.update(status="provisioned", bindings_file=str(output / "bindings.json"),
                      object_storage="shared_with_declared_monorepo_keep_source_available")
        _write(output / "provision-report.json", report)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["status"] = "failed"
        report["reason"] = str(exc)
        try:
            receipt = output / "provision-report.json"
            if receipt.exists():
                # An interrupted publication or another operator's file is not
                # overwritten. Keep a separate failed final occurrence receipt.
                receipt = output / "provision-failure.json"
            report["failure_receipt"] = str(receipt)
            _write(receipt, report)
        except (OSError, ValueError) as write_error:
            report["receipt_write_error"] = str(write_error)
        raise ProvisionFailure(report) from exc
    return report


def load_bindings_file(path: Path, *, workflow: str | None = None) -> dict:
    """Read an explicit operator file; saved investigations never call this."""
    value = _read(path)
    if (type(value) is not dict or set(value) != {"schema", "workflow", "bindings", "source", "providers", "qualification", "authorizes_execution"}
            or value["schema"] != BINDINGS_SCHEMA or value["qualification"] != "not_performed" or value["authorizes_execution"] is not False):
        raise ValueError("Unsupported operator bindings envelope or authority claim")
    kind = value["workflow"]
    if workflow is not None and kind != workflow:
        raise ValueError("Operator bindings select a different workflow")
    requirements = workflow_requirements(kind)
    paths = _paths(value["bindings"])
    declared_roles = set(requirements["providers"]) | set(requirements["external_bindings"])
    required_roles = declared_roles - ({"cbsr"} if kind == "telemetry" else set())
    if not required_roles <= set(paths) <= declared_roles:
        raise ValueError("Operator binding roles differ from the selected workflow")
    source = value["source"]
    if (type(source) is not dict or set(source) != {"root", "revision", "source_tree"}
            or not _SHA.fullmatch(source.get("revision", "")) or not _SHA.fullmatch(source.get("source_tree", ""))):
        raise ValueError("Operator bindings require their declared source identities")
    _paths({"monorepo": source["root"]})
    providers = value["providers"]
    selected_providers = set(paths) & set(requirements["providers"])
    if type(providers) is not dict or set(providers) != selected_providers:
        raise ValueError("Operator bindings require every original provider declaration")
    for role in selected_providers:
        pin = requirements["providers"][role]
        row = providers[role]
        fields = {"revision", "source_tree", "license", "repository", "module", "source_root",
                  "dependencies", "requires_python", "dependency_identity"}
        if (type(row) is not dict or not fields <= set(row) <= fields | {"build_system"} or row.get("revision") != pin["revision"]
                or not _SHA.fullmatch(row.get("source_tree", "")) or pin.get("source_tree", row["source_tree"]) != row["source_tree"]):
            raise ValueError("Operator bindings contain a different original provider identity")
    return {**value, "bindings": paths}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="net provision", description=__doc__)
    parser.add_argument("--list", action="store_true", help="List installed workflow pin and external-binding declarations")
    parser.add_argument("--plan", action="store_true", help="Inspect local retained objects and prerequisites without creating files")
    parser.add_argument("--monorepo", type=Path)
    parser.add_argument("--workflow", choices=sorted(OPERATIONS))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--binding", action="append", default=[], metavar="ROLE=ABSOLUTE_PATH",
                        help="Explicit non-repository engine/runtime prerequisite; never a provider pin override")
    args = parser.parse_args(argv)
    try:
        from .scientific import parse_bindings
        bindings = parse_bindings(args.binding)
        if args.list:
            if any((args.plan, args.monorepo, args.workflow, args.output_dir, bindings)):
                raise ValueError("--list cannot accept provisioning arguments")
            result = catalog()
        else:
            if args.monorepo is None or args.workflow is None:
                raise ValueError("Supply an explicit --monorepo and --workflow")
            if args.plan:
                if args.output_dir is not None:
                    raise ValueError("A read-only plan does not accept --output-dir")
                result = plan(args.monorepo, args.workflow, bindings=bindings)
            else:
                if args.output_dir is None:
                    raise ValueError("Provisioning requires a new --output-dir")
                result = provision(args.monorepo, args.workflow, args.output_dir, bindings=bindings)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if result.get("status") == "blocked" else 0
    except ProvisionFailure as exc:
        print(json.dumps(exc.report, indent=2, allow_nan=False), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, RecursionError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
