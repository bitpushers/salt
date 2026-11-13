# Operations

## Running it

```sh
salt daemon            # console + scheduler in one process, 127.0.0.1:9131
salt serve             # console only
salt coordinator status
```

One process is deliberate: the scheduler and the console share the store, and a second
process would only add a race. Dispatch is off until enabled on the Coordinator page.

## Keeping it running

macOS — copy `scripts/com.bitpushers.salt.plist` to `~/Library/LaunchAgents/`, replace
`__SALT_BINARY__` (e.g. the `salt` on your PATH or a checkout's `.venv/bin/salt`),
`__SALT_CHECKOUT__` and `__HOME__`, then:

```sh
launchctl load -w ~/Library/LaunchAgents/com.bitpushers.salt.plist
```

Linux — a systemd user unit:

```ini
[Unit]
Description=Salt coordinator and console
After=network.target

[Service]
WorkingDirectory=%h/src/salt
ExecStart=%h/src/salt/.venv/bin/salt daemon --port 9131
Restart=always

[Install]
WantedBy=default.target
```

## Where things live

| what | where |
|---|---|
| config | `$XDG_CONFIG_HOME/salt/config.toml` (else `~/.config/salt/`) |
| database | `$XDG_DATA_HOME/salt/salt.sqlite` (else `~/.local/share/salt/`) |
| briefs and run logs | `<data dir>/runs/` |

The database is disposable: delete it, run `salt ingest` for each project, and the board
comes back. Runs, approvals and events do not come back — that is the point of them.

## Upgrading

`uv tool install saltsh --force` (or reinstall from a checkout) then restart the service.
If you run the daemon from a checkout's virtualenv, an editable install is enough and a
restart applies your changes.

**A tool install copies the package.** Editing files in a checkout does not change what an
installed `salt` serves — reinstall or run from the checkout's `.venv`.

## Backups

One file: `salt.sqlite`. Copy it while the daemon is stopped, or use `sqlite3 .backup`.
Nothing in it is secret.

## Troubleshooting

- **The board is empty** — `salt ingest /path/to/repo`, and check the repo has
  `features/**/*.feature.yaml`.
- **Everything shows as untested** — an ACID counts as a test reference only when it
  starts a standalone comment line, or appears in a JSON `_acid` array. A trailing
  comment after code is not counted.
- **A phase will not advance** — it needs a passing receipt (`salt prove`) or, for a gate,
  an approval. `salt query feature <id>` shows what is recorded.
- **Nothing is dispatching** — dispatch is off by default; enable it on the Coordinator
  page. Then check the Queue page for live leases and stranded units.
