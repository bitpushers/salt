"""The command line: the contract bots use.

`prove` is the important verb — it runs the command and captures evidence, and refuses to
record success when the command fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as config_mod
from . import coordinator, pipeline
from .config import Config
from .ingest import ingest_repository
from .receipts import run_command
from .store import Store

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2


def _open(args) -> tuple[Config, Store]:
    config = config_mod.load()
    if getattr(args, "data_dir", None):
        config.data_dir = Path(args.data_dir).expanduser()
    return config, Store(config.db_path)


def _profile_for(config: Config, project: str | None) -> pipeline.Profile:
    name = config.profile
    if project:
        entry = config.project(project)
        if entry:
            name = entry.profile
    try:
        return pipeline.load_profile(name)
    except pipeline.ProfileError:
        return pipeline.load_profile(pipeline.STANDARD)


def _feature(store: Store, text: str):
    feature = store.find_feature(text)
    if feature is None:
        print(f"salt: no feature matching {text!r}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    return feature


def _fail(message: str) -> int:
    print(f"salt: {message}", file=sys.stderr)
    return EXIT_REFUSED


# -- commands -------------------------------------------------------------
def cmd_init(args) -> int:
    config, store = _open(args)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    path = config_mod.save(config)
    print(f"config: {path}")
    print(f"database: {config.db_path}")
    if args.project:
        repo = Path(args.project).expanduser().resolve()
        name = args.name or repo.name
        config.add_project(config_mod.Project(name=name, repo_path=str(repo)))
        config_mod.save(config)
        print(f"registered project {name!r} -> {repo}")
        print("next: salt ingest " + str(repo))
    return EXIT_OK


def cmd_ingest(args) -> int:
    config, store = _open(args)
    repo = Path(args.path).expanduser().resolve()
    name = args.name or repo.name
    profile = _profile_for(config, name)
    if config.project(name) is None:
        config.add_project(config_mod.Project(name=name, repo_path=str(repo)))
        config_mod.save(config)
    result = ingest_repository(store, profile, name, repo)
    print(
        f"{result['project']}: {result['features']} feature(s), "
        f"{result['acids']} ACID(s), {result['refs']} reference(s)"
    )
    if result["removed"]:
        print(f"removed {len(result['removed'])} stale feature(s)")
    return EXIT_OK


def cmd_role_list(args) -> int:
    config, store = _open(args)
    profile = _profile_for(config, args.project)
    for role in profile.roles:
        marker = " (coordinator)" if role.coordinator else ""
        owns = [p.name for p in profile.phases if p.owner == role.name]
        print(f"{role.name}{marker}")
        print(f"  owns: {', '.join(owns) or '-'}")
        print(f"  runner: {role.runner}")
    return EXIT_OK


def cmd_next(args) -> int:
    config, store = _open(args)
    profile = _profile_for(config, None)
    units = coordinator.eligible(store, profile)
    if args.as_role:
        units = [unit for unit in units if unit.role == args.as_role]
    if not units:
        print("nothing eligible")
        return EXIT_OK
    for unit in units:
        print(f"{unit.feature_id} {unit.phase} -> {unit.role}")
    return EXIT_OK


def cmd_claim(args) -> int:
    config, store = _open(args)
    feature = _feature(store, args.feature)
    profile = _profile_for(config, feature["project"])
    definition = profile.phase(args.phase)
    if definition is None:
        return _fail(f"unknown phase {args.phase!r}")
    unit = coordinator.Unit(
        feature_id=feature["id"],
        project=feature["project"],
        feature_name=feature["name"],
        phase=args.phase,
        role=definition.owner,
    )
    try:
        claim_id = coordinator.claim(
            store, profile, unit, actor=args.as_role or "cli", ttl_seconds=args.ttl
        )
    except pipeline.TransitionError as exc:
        return _fail(str(exc))
    print(f"claimed {feature['id']}/{args.phase} (claim {claim_id}, {args.ttl}s)")
    return EXIT_OK


def cmd_phase(args) -> int:
    config, store = _open(args)
    feature = _feature(store, args.feature)
    profile = _profile_for(config, feature["project"])
    try:
        state = pipeline.set_phase(
            store,
            profile,
            feature["id"],
            args.phase,
            args.state,
            actor=args.as_role or "cli",
            role=args.as_role,
        )
    except pipeline.TransitionError as exc:
        return _fail(str(exc))
    print(f"{feature['id']}/{args.phase}: {state}")
    return EXIT_OK


def cmd_prove(args) -> int:
    config, store = _open(args)
    feature = _feature(store, args.feature)
    profile = _profile_for(config, feature["project"])
    project = store.project(feature["project"])
    cwd = Path(project["repo_path"]) if project else Path.cwd()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        return _fail("nothing to run; usage: salt prove FEATURE PHASE --as ROLE -- CMD")

    receipt = run_command(command, cwd=cwd, timeout=args.timeout)
    store.add_receipt(
        feature["id"],
        args.phase,
        receipt.command,
        receipt.cwd,
        receipt.exit_code,
        receipt.commit_sha,
        receipt.digest,
        receipt.output_tail,
        args.as_role or "cli",
        args.as_role,
    )
    if not receipt.ok:
        print(receipt.output_tail)
        return _fail(
            f"{receipt.command!r} exited {receipt.exit_code}; the phase stays where it is"
        )
    try:
        pipeline.set_phase(
            store,
            profile,
            feature["id"],
            args.phase,
            "done",
            actor=args.as_role or "cli",
            role=args.as_role,
        )
    except pipeline.TransitionError as exc:
        return _fail(str(exc))
    print(f"{feature['id']}/{args.phase}: done ({receipt.command}, sha {receipt.commit_sha})")
    return EXIT_OK


def cmd_approve(args) -> int:
    config, store = _open(args)
    feature = _feature(store, args.feature)
    profile = _profile_for(config, feature["project"])
    try:
        pipeline.approve(
            store,
            profile,
            feature["id"],
            args.gate,
            args.decision,
            note=args.note or "",
            actor=args.as_role or "human",
            channel="cli",
        )
    except pipeline.TransitionError as exc:
        return _fail(str(exc))
    print(f"{feature['id']}/{args.gate}: {args.decision}")
    return EXIT_OK


def cmd_query(args) -> int:
    config, store = _open(args)
    profile = _profile_for(config, args.project)
    if args.what == "features":
        out = []
        for feature in store.features(args.project):
            states = store.phase_states(feature["id"])
            current = next(
                (
                    name
                    for name in profile.phase_names
                    if (states[name]["state"] if name in states else "pending") != "done"
                ),
                None,
            )
            out.append(
                {
                    "id": feature["id"],
                    "phase": current,
                    "coverage": pipeline.acid_coverage(store, feature["id"]),
                }
            )
        print(json.dumps(out, indent=2))
    elif args.what == "feature":
        feature = _feature(store, args.name or "")
        print(json.dumps(dict(feature), indent=2, default=str))
    elif args.what == "events":
        print(json.dumps([dict(row) for row in store.events(50)], indent=2, default=str))
    elif args.what == "runs":
        print(json.dumps([dict(row) for row in store.runs(50)], indent=2, default=str))
    return EXIT_OK


def cmd_coordinator(args) -> int:
    config, store = _open(args)
    profile = _profile_for(config, None)
    if args.action == "status":
        live = store.live_claims()
        print(f"interval: {coordinator.interval_seconds(store, profile)}s")
        print(f"concurrency: {coordinator.concurrency(store, profile)}")
        print(f"dispatch: {'enabled' if coordinator.dispatch_enabled(store) else 'disabled'}")
        print(f"last tick: {store.get_setting(coordinator.SETTING_LAST_TICK) or 'never'}")
        print(f"live leases: {len(live)}")
        for row in live:
            print(f"  {row['feature_id']}/{row['phase']} -> {row['role']} until {row['expires_at']}")
    elif args.action == "set-interval":
        coordinator.set_interval(store, args.seconds)
        print(f"interval: {args.seconds}s")
    elif args.action == "enable":
        coordinator.set_dispatch_enabled(store, True)
        print("dispatch: enabled")
    elif args.action == "disable":
        coordinator.set_dispatch_enabled(store, False)
        print("dispatch: disabled")
    elif args.action == "run-now":
        from .daemon import Daemon

        result = Daemon(config, store, profile, verbose=True).tick()
        print(json.dumps(result, indent=2, default=str))
    return EXIT_OK


def cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app
    from .daemon import Daemon

    config, store = _open(args)
    profile = _profile_for(config, None)
    daemon = None if args.no_coordinator else Daemon(config, store, profile, verbose=True)
    app = create_app(config, store, profile, daemon)
    if daemon and args.with_scheduler:
        import threading

        threading.Thread(target=daemon.run_forever, daemon=True).start()
    uvicorn.run(app, host=config.host, port=args.port or config.port, log_level="info")
    return EXIT_OK


def cmd_daemon(args) -> int:
    import threading

    import uvicorn

    from .api import create_app
    from .daemon import Daemon

    config, store = _open(args)
    profile = _profile_for(config, None)
    daemon = Daemon(config, store, profile, verbose=True)
    app = create_app(config, store, profile, daemon)
    threading.Thread(target=daemon.run_forever, daemon=True).start()
    print(f"salt daemon: http://{config.host}:{args.port or config.port}")
    uvicorn.run(app, host=config.host, port=args.port or config.port, log_level="warning")
    return EXIT_OK


# -- parser ---------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="salt",
        description="Salt — a local, spec-driven build system with receipts, gates and a roster.",
    )
    parser.add_argument("--data-dir", help="override the data directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create config and database")
    p.add_argument("--project", help="register this repository")
    p.add_argument("--name", help="project name")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="scan a repository into the database")
    p.add_argument("path")
    p.add_argument("--name", help="project name (defaults to the directory name)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("role", help="inspect the roster")
    p.add_argument("action", choices=["list"])
    p.add_argument("--project")
    p.set_defaults(func=cmd_role_list)

    p = sub.add_parser("next", help="list eligible units")
    p.add_argument("--as", dest="as_role")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("claim", help="lease a unit")
    p.add_argument("feature")
    p.add_argument("phase")
    p.add_argument("--as", dest="as_role")
    p.add_argument("--ttl", type=int, default=1800)
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("phase", help="set a phase state")
    p.add_argument("feature")
    p.add_argument("phase")
    p.add_argument("--state", required=True, choices=["pending", "started", "blocked", "done"])
    p.add_argument("--as", dest="as_role")
    p.set_defaults(func=cmd_phase)

    p = sub.add_parser("prove", help="run a command, record a receipt, mark the phase done")
    p.add_argument("feature")
    p.add_argument("phase")
    p.add_argument("--as", dest="as_role")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_prove)

    p = sub.add_parser("approve", help="record a human decision on a gate")
    p.add_argument("feature")
    p.add_argument("gate")
    p.add_argument("--decision", default="approved", choices=["approved", "rejected"])
    p.add_argument("--note", default="")
    p.add_argument("--as", dest="as_role", default="human")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("query", help="machine-readable output")
    p.add_argument("what", choices=["features", "feature", "events", "runs"])
    p.add_argument("name", nargs="?")
    p.add_argument("--project")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("coordinator", help="the dispatcher")
    p.add_argument("action", choices=["status", "run-now", "set-interval", "enable", "disable"])
    p.add_argument("seconds", nargs="?", type=int, default=300)
    p.set_defaults(func=cmd_coordinator)

    p = sub.add_parser("serve", help="the console, without the scheduler")
    p.add_argument("--port", type=int)
    p.add_argument("--no-coordinator", action="store_true")
    p.add_argument("--with-scheduler", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("daemon", help="the console and the scheduler in one process")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_daemon)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except config_mod.ConfigError as exc:
        print(f"salt: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except pipeline.ProfileError as exc:
        print(f"salt: profile: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except FileNotFoundError as exc:
        print(f"salt: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
