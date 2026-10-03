"""Read-only operator diagnostics without importing the scientific CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .doctor import PROFILES, diagnose


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="net doctor",
        description="Inspect explicit local provider identities without running or installing them",
    )
    parser.add_argument("--profile", choices=PROFILES, default="core")
    parser.add_argument("--stack-root", type=Path, help="Declared-workload role directories sra and scr")
    parser.add_argument("--engine", type=Path, help="Explicit declared-workload execution engine")
    parser.add_argument("--binding", type=Path, help="Existing native, reaction or interval runtime binding JSON")
    args = parser.parse_args(argv)
    report = diagnose(args.profile, stack_root=args.stack_root, engine=args.engine, binding=args.binding)
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["status"] == "preflight_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
