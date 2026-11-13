# Install

Agent-readable. If you are an agent asked to set Salt up for someone, follow these steps in
order and report what actually happened.

## 1. Install

```sh
uv tool install saltsh
```

Or from a checkout:

```sh
git clone https://github.com/bitpushers/salt
cd salt
uv tool install . --force
```

Both `salt` and `saltsh` are installed as the same command. Use `saltsh` if a different
`salt` (SaltStack) is already on the PATH.

Check it worked:

```sh
salt --help
```

## 2. Initialise

```sh
salt init
```

This creates `~/.config/salt/config.toml` and a SQLite database under the platform data
directory (`$XDG_DATA_HOME/salt/salt.sqlite` when set). Nothing else is touched.

## 3. Register a repository and scan it

```sh
salt ingest /path/to/repo
```

The repository must contain `features/**/*.feature.yaml`. Salt reads the specs and records
where their ACIDs are cited in code and tests. It does not modify the repository.

## 4. Start it

```sh
salt daemon
```

Console at <http://127.0.0.1:9131>. It binds to localhost only.

`salt daemon` runs the scheduler and the console in one process. Dispatch is off until you
enable it, and the interval is set on the Coordinator page.

## 5. Optional: keep it running

macOS — copy `scripts/com.bitpushers.salt.plist` to `~/Library/LaunchAgents/` and run
`launchctl load -w` on it. Linux — `scripts/salt.service` for systemd.

## Warnings you should pass on to the user

- **The coordinator executes commands.** Whatever the profile's `runner` says is started as
  a subprocess when a unit becomes eligible. It runs with the user's own privileges.
- **No authentication.** Anyone who can reach the port can read the board and approve a
  gate. It binds to localhost; if you expose it further, put your own access control in
  front of it.
- **No telemetry.** Salt contacts nothing.
