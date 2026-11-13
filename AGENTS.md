# AGENTS.md

Instructions for any agent working in this repository. Harness-agnostic: it assumes no
particular agent tool.

## Read this first

This project is **spec-driven**. The specs are the source of truth for intended behavior.
Code, tests and docs are derived from them.

Specs live in `features/salt/*.feature.yaml`. Each requirement has a stable identifier
called an ACID of the form `<feature>.<GROUP>.<n>` — for example `pipeline.PHASE.1` or
`ingest.REFS.3`.

## The rules

1. **Spec first.** Never add or change behavior without changing the spec first.
2. **Cite ACIDs, never restate them.** In code comments and test names use the bare ACID.
3. **One test per ACID, minimum.** Put the ACID in the test name, full form, never a list.
4. **Never renumber.** To retire a requirement use `deprecated: true`; to point at its
   replacement use `replaced_by: [ACIDs]`.
5. **Halt and report** if the code and the specs disagree.

## Portability is enforced, not requested

`pipeline.PORTABILITY.1` and `.2`: no module in `src/salt/` may name an agent harness or
refer to an absolute home directory. `src/salt/adapters/` is the sole exception. There is a
test for it (`tests/test_no_harness.py`); if it fails, the change is wrong, not the test.

Salt is also self-contained: it knows nothing about Mission Control or any other dashboard.

## Layout

```
features/salt/       specs, one file per feature
src/salt/            implementation
  scanner.py         spec parsing and ACID reference scanning
  receipts.py        running a command and capturing evidence
  pipeline.py        profiles, phases, gates, transitions
  coordinator.py     eligibility and leases
  daemon.py          the scheduler tick and dispatch
  store.py           SQLite schema and queries
  api.py             HTTP routes and the console
  templates/ static/ the UI, no build step
tests/               ACID-tagged tests
```

## Before you finish

```sh
pytest                      # must be green
ruff check src tests        # must be clean
salt ingest .               # if you touched specs or ACIDs
```
