"""Convenient local startup through the existing CIW Session and serve command.

This entry point adds read-only dependency diagnostics and operator instructions.
Provider bindings and scientific execution remain owned by ``ciw serve``.
"""
from __future__ import annotations

from pathlib import Path
import sys


DEFAULT_OUTPUT_DIR = Path(".ciw")


def _core_preflight() -> bool:
    from .doctor import diagnose

    report = diagnose("core")
    if report["status"] == "preflight_passed":
        return True
    print("NET workbench startup blocked by core dependency checks:", file=sys.stderr)
    for check in report["checks"]:
        if check["status"] == "failed":
            print(f"  {check['check']}: {check.get('reason', check['classification'])}",
                  file=sys.stderr)
    print("From this repository, install the core package with python -m pip install -e .",
          file=sys.stderr)
    print("Then inspect dependencies with ciw doctor --profile core and retry net workbench.",
          file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    """Start one ordinary Session; all existing serve options remain available."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not _core_preflight():
        return 2

    from . import cli

    # A default placed before the operator's arguments lets argparse retain its
    # own option spelling, abbreviation and last-value behavior. Explicit source
    # options are parsed by the existing mutually exclusive serve group.
    serve_arguments = ["serve", "--output-dir", str(DEFAULT_OUTPUT_DIR), *arguments]
    args = cli.parser().parse_args(serve_arguments)
    if not 1 <= args.port <= 65535:
        # Delegate the existing refusal and exit status before startup messages.
        return cli.main(serve_arguments)
    if args.workspace is None and args.recording is None and not args.resume:
        serve_arguments.append("--resume")

    url = f"ws://127.0.0.1:{args.port}"
    print("NET workbench: core dependency preflight passed; scientific qualification not performed.")
    print(f"Workspace directory: {args.output_dir}")
    if args.workspace is not None:
        print(f"Reopen workspace: {args.workspace}")
    elif args.recording is not None:
        print(f"Open recording: {args.recording}")
    else:
        print(f"Resume {args.output_dir / 'workspace.json'} when present; otherwise start synthetic oscillator evidence.")
    print("In a second terminal after the service starts:")
    print(f"  ciw health --url {url}")
    print(f"  ciw send session.get --url {url}")
    print(f"  ciw send operation.list --url {url}")
    print(f"  ciw send result.list --url {url}")
    print(f"  ciw send workspace.save --url {url}")
    print("  ciw capabilities list")
    print("Optional providers require explicit serve bindings; unavailable operations remain unavailable.",
          flush=True)
    return cli.main(serve_arguments)


if __name__ == "__main__":
    raise SystemExit(main())
