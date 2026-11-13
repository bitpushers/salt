"""The scheduler: tick, decide, dispatch.

One process runs the coordinator loop and the console. Dispatch is off until it is
enabled, and the interval and concurrency come from settings the console writes.

A dispatch starts the role's `runner` command as a subprocess with the brief path
substituted. The run is recorded. A clean exit does not advance a phase: the bot still
has to call `salt prove`.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import coordinator, pipeline
from .config import Config
from .store import Store, now

BRIEF = """# Salt brief — {project}:{feature} / {phase}

You are the **{role}** role. This file is your whole assignment; the specs are the
source of truth and the ACIDs below are the requirements you are accountable for.

- project: `{project}`
- feature: `{feature}` (`{feature_id}`)
- phase: `{phase}`
- spec file: `{spec_path}`
- repository: `{repo_path}`
- lease: {ttl} seconds, then this unit returns to the queue

## What "done" means for this phase

{evidence}

## ACIDs in scope

{acids}

## Rules

1. Do not change a requirement without changing the spec first.
2. Cite ACIDs in test names and in code comments on their own line, never inline.
3. Prove your work with evidence, not a claim:

   ```sh
   salt prove {feature_id} {phase} --as {role} -- <your test command>
   ```

   A non-zero exit leaves the phase where it is.
4. If you cannot proceed, say so in your output. Do not mark the phase done.

## When you finish

Run the prove command above. If it passes, the receipt is recorded and the phase is done;
if it fails, report the failure and leave the unit to expire.
"""

EVIDENCE = {
    "intake": "the refinement questions are answered — record a note on the feature.",
    "spec": "the spec exists and validates; prove it by running the validator.",
    "spec_push": "the spec is committed and pushed; prove it with the git command.",
    "implement": "code citing these ACIDs exists in a commit.",
    "tests": "the test command exits zero and every ACID has at least one test reference.",
    "verify": "the built thing runs and responds; prove it with the boot or curl command.",
    "commit": "the work is pushed; prove it with `git push`.",
    "sync": "state is written and counts reconcile; prove it with the ingest command.",
}


class Daemon:
    def __init__(
        self,
        config: Config,
        store: Store,
        profile: pipeline.Profile,
        verbose: bool = False,
    ):
        self.config = config
        self.store = store
        self.profile = profile
        self.verbose = verbose
        self.children: dict[int, subprocess.Popen] = {}
        self.runs_dir = config.runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # -- dispatch ---------------------------------------------------------
    def write_brief(self, unit: coordinator.Unit, ttl: int) -> Path:
        feature = self.store.feature(unit.feature_id)
        project = self.store.project(unit.project)
        acids = self.store.acids(unit.feature_id)
        counts = self.store.ref_counts(unit.feature_id)
        lines = []
        for acid in acids:
            count = counts.get(acid["id"], {})
            lines.append(
                f"- `{acid['id']}` — {acid['text']} "
                f"(code refs: {count.get('code', 0)}, test refs: {count.get('test', 0)})"
            )
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = unit.feature_id.replace(":", "-").replace("/", "-")
        path = self.runs_dir / f"{stamp}-{slug}-{unit.phase}.brief.md"
        path.write_text(
            BRIEF.format(
                project=unit.project,
                feature=unit.feature_name,
                feature_id=unit.feature_id,
                phase=unit.phase,
                role=unit.role,
                spec_path=feature["file_path"] if feature else "",
                repo_path=project["repo_path"] if project else "",
                ttl=ttl,
                evidence=EVIDENCE.get(unit.phase, "evidence is recorded by `salt prove`."),
                acids="\n".join(lines) or "- (this spec has no ACIDs)",
            ),
            encoding="utf-8",
        )
        return path

    def role_runner(self, role: str | None) -> str:
        definition = self.profile.role(role)
        return definition.runner if definition else "cat {brief}"

    def dispatch(self, unit: coordinator.Unit, ttl: int) -> dict:
        """Lease the unit, write its brief, start its runner."""
        project = self.store.project(unit.project)
        repo_path = project["repo_path"] if project else "."

        claim_id = None
        claim_id = coordinator.claim(
            self.store,
            self.profile,
            unit,
            actor=f"coordinator:{unit.role}",
            ttl_seconds=ttl,
        )
        brief = self.write_brief(unit, ttl)
        command = self.role_runner(unit.role).replace("{brief}", shlex.quote(str(brief)))
        log_path = brief.with_suffix(".log")

        with log_path.open("wb") as log:
            try:
                process = subprocess.Popen(
                    shlex.split(command),
                    cwd=repo_path,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                pid = process.pid
                self.children[pid] = process
                exit_code = None
            except OSError as exc:
                log.write(f"failed to start runner: {exc}\n".encode())
                pid = None
                exit_code = 127

        run_id = self.store.add_run(
            unit.feature_id,
            unit.phase,
            unit.role,
            f"coordinator:{unit.role}",
            command,
            pid,
            str(log_path),
            str(brief),
        )
        if exit_code is not None:
            self.store.finish_run(run_id, exit_code)
            self.store.release_claim(claim_id)
        self.store.event(
            "run",
            f"{unit.feature_id}/{unit.phase}",
            f"{unit.role} started (run {run_id})",
        )
        return {
            "run_id": run_id,
            "unit": f"{unit.feature_id}/{unit.phase}",
            "role": unit.role,
            "pid": pid,
            "brief": str(brief),
        }

    def reap(self) -> list[dict]:
        """Collect finished children and release their leases."""
        finished: list[dict] = []
        for pid, process in list(self.children.items()):
            code = process.poll()
            if code is None:
                continue
            del self.children[pid]
            rows = [
                row
                for row in self.store.open_runs()
                if row["pid"] == pid
            ]
            for row in rows:
                self.store.finish_run(row["id"], code)
                claim = self.store.live_claim(row["feature_id"], row["phase"])
                if claim:
                    self.store.release_claim(claim["id"])
                self.store.event(
                    "run",
                    f"{row['feature_id']}/{row['phase']}",
                    f"{row['role']} exited {code}",
                )
                finished.append(
                    {
                        "feature_id": row["feature_id"],
                        "phase": row["phase"],
                        "exit_code": code,
                    }
                )
        return finished

    # -- the tick ---------------------------------------------------------
    def tick(self) -> dict:
        expired = coordinator.expire(self.store)
        finished = self.reap()
        enabled = coordinator.dispatch_enabled(self.store)
        dispatched: list[dict] = []
        skipped: list[dict] = []

        if enabled:
            ttl = max(300, coordinator.interval_seconds(self.store, self.profile) * 4)
            limit = coordinator.concurrency(self.store, self.profile)
            for unit in coordinator.eligible(self.store, self.profile):
                busy = self.store.running_count(unit.role)
                if busy >= limit:
                    skipped.append({"unit": f"{unit.feature_id}/{unit.phase}", "why": "at concurrency limit"})
                    continue
                dispatched.append(self.dispatch(unit, ttl))

        self.store.set_setting(coordinator.SETTING_LAST_TICK, now())
        return {
            "expired": expired,
            "finished": finished,
            "dispatched": dispatched,
            "skipped": skipped,
            "enabled": enabled,
            "at": now(),
        }

    def run_forever(self) -> None:
        while True:
            try:
                result = self.tick()
                if self.verbose and (result["dispatched"] or result["finished"]):
                    print(
                        f"{result['at']} dispatched={len(result['dispatched'])} "
                        f"finished={len(result['finished'])} expired={len(result['expired'])}"
                    )
            except Exception as exc:  # keep the loop alive; a bad tick must not stop the daemon
                self.store.event("error", "coordinator", f"tick failed: {exc}")
                if self.verbose:
                    print(f"tick failed: {exc}")
            time.sleep(coordinator.interval_seconds(self.store, self.profile))
