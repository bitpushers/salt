# Salt

**A local, spec-driven build system with a bot roster, receipts, and gates.**

Salt reads the specs in your repository (`features/**/*.feature.yaml`), tracks every
acceptance criterion (an *ACID*) across the code and tests that cite it, and records
*receipts* — the command that was run, its exit code, and the commit SHA — as evidence
that a phase of work actually happened. A phase is done when the evidence exists, not
when a bot says so.

A salt is what you get when an acid meets a base. Here the acid is your ACIDs and the
base is your codebase; the salt is the state you can actually see.

```
salt init                     # config, data dir, first project
salt ingest ~/src/myproject   # scan specs, code, tests
salt daemon                   # coordinator + web UI on 127.0.0.1:9131
```

Then open <http://127.0.0.1:9131>.

## What it does

- **Specs stay in git.** Salt never owns spec text and never edits requirements.
- **Receipts, not assertions.** `salt prove` runs the command, captures the exit code,
  the commit SHA and a digest of the output. A failing command cannot mark a phase done.
- **Human gates.** Two phases are approvals, recorded separately with the channel (web or
  CLI) so the history does not lie about who clicked.
- **A roster.** Roles own phases; a role that does not own a phase is refused. Identity is
  name-only (`--as implementer`) — leases are advisory locks, not a security boundary.
- **A coordinator that runs.** `salt daemon` ticks on an interval you set in the UI,
  writes a brief for each eligible unit, and starts the role's configured runner command.
  A run exiting clean does not advance anything by itself: the bot still has to prove.
- **Self-contained.** No hosted service, no accounts, no telemetry. One process, one
  SQLite file, bound to localhost.

## Status

Early. The phase machine, receipts, roster, coordinator and UI are the v1 core. MCP and
harness adapters come later. It is in daily use on its own repository and on the first
project it was built to serve.

## License

**License: not yet decided.** This project is not licensed for redistribution at this
time. The intention is to make it freely available for others to use. If you need a
specific license to adopt it — commercially, institutionally, or in any context that
requires one — please open a GitHub Issue describing your use, and we will resolve it.

## Requirements

Python 3.11 or newer. Nothing else; `saltsh` is the package name on PyPI because `salt`
belongs to an unrelated automation project.

```sh
uv tool install saltsh    # installs the `salt` and `saltsh` commands
```

See [INSTALL.md](INSTALL.md) for the agent-readable version, and
[docs/profiles.md](docs/profiles.md) for the workflow profile format.
