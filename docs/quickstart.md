# Start the NET instrument

NET provides one local workbench for retained scientific investigations. Start
with its built-in synthetic workflows, then provision a specialist workflow
when its inputs and runtime are available. The monorepo's 21 public modules
retain their own packages, execution pins and qualification boundaries.

## Install

Use Python 3.11 or newer from the repository root. A normal installation keeps
the installed package separate from subsequent source edits.

On Windows, in PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\Activate.ps1
```

If activation is restricted, use `.venv\Scripts\net.exe` and
`.venv\Scripts\ciw.exe` for the commands below. No administrator access or
execution-policy change is needed.

On Linux or macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
source .venv/bin/activate
```

For development, use `python -m pip install -e '.[dev]'` instead. Signed
Legibility workflows additionally require the `legibility` extra:
`python -m pip install '.[legibility]'`. Specialist provider dependencies and
native engines are provisioned for their selected workflow separately.

## Run the first-use check

```sh
net doctor --profile core
net catalog
net start --output-dir results/first-use-001
```

`doctor` checks local package and dependency metadata. `catalog` lists command
surfaces and provider requirements without running them. `start` explicitly
executes bounded synthetic oscillator, thermal-observer and atmosphere
workflows, checks their results, replays the thermal observer and inspects the
saved records. Use a new output directory for each first-use check.

Read `results/first-use-001/readiness.json` for the completion status, named
checks and artifact paths. It retains distinct evidence, execution, result and
verification identities. Keep that directory as the record of this
installation's check. A successful receipt covers the checks that actually ran;
the [operator readiness guide](OPERATOR_READINESS.md) describes additional
provider setup and qualification.

## Use the persistent local workbench

```sh
net workbench --output-dir results/workbench --port 8765
```

Leave that terminal running. The workbench creates a synthetic session on
first use and reopens its saved workspace on later starts. Reopening restores
retained sources and results without executing them again.

From a second activated terminal:

```sh
ciw health
ciw send session.get
ciw send analysis.stats
ciw send result.list
ciw send workspace.save
```

`analysis.stats` creates a new result with its execution identity; the other commands inspect
or save the local session. Stop the server with Ctrl+C. Save explicitly before
stopping when you need a checkpoint, especially on Windows. For another port,
pass the same `--url ws://127.0.0.1:PORT` to client commands.

The service binds to loopback by default. The optional Godot client attaches
to the same session; follow the [Godot guide](../godot/README.md) for its exact
version and connection instructions.

## Analyze a retained recording

The existing `ciw` commands remain available:

```sh
ciw demo --output recordings/demo.json
ciw analyze stats --recording recordings/demo.json --channel q --start 2 --end 8 --output-dir results/stats
ciw analyze spectrum --recording recordings/demo.json --channel q --start 0 --end 12 --output-dir results/spectrum
ciw inspect results/spectrum/workspace.json
```

The example contains 768 synthetic damped-oscillator samples on `[0, 12)`
seconds. Statistics and the one-sided periodic-Hann periodogram use the retained
arrays. `inspect` reads the saved workspace without repeating the calculation.
Keep separate output directories for distinct investigations.

Selection changes use the current revision returned by `session.get`:

```sh
ciw send selection.update --payload-file selection.json
```

The payload file contains, for example,
`{"expected_revision":0,"channel":"q","interval_s":[2,8]}`. Replace the revision
with the current value; a conflict means another client changed the selection.

## Select a specialist workflow

Use `net catalog --json` for machine-readable discovery and the
[operator readiness guide](OPERATOR_READINESS.md) for explicit provider setup.
The [atmospheric engine](ATMOSPHERIC_ENGINE.md) and
[elastic contact benchmark](IMPACT_TESTBED.md) have bounded example/run/inspect/
verify/export commands. The [shared workbench guide](WORKBENCH_ASSEMBLY.md)
covers calibrated observations and specialist provider operations.

Local JSON persistence, retained failure history and explicit replay are
implemented. A local WebSocket session accepts bounded JSON frames up to
8 MiB; individual workload and artifact contracts impose their own tighter
limits. Remote authentication, multi-user service operation and hard real-time
hardware control require separate deployment and qualification.

For operation contracts, exact provider pins and qualification scope, use the
[integration coverage matrix](INTEGRATION_COVERAGE.md), [protocol](PROTOCOL.md),
[deployment guide](../deploy/README.md) and [development checks](DEVELOPMENT.md).
