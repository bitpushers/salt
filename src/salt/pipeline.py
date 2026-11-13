"""Profiles, phases, gates, and the transitions between them.

pipeline.PHASE.* — a phase is done only when its evidence exists.
pipeline.GATE.* — a gate advances only on an approval, never on a receipt.
pipeline.ROSTER.* — roles own phases and carry a runner command template.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_DIR = Path(__file__).parent / "profiles"
STANDARD = "standard"
HUMAN = "human"


class ProfileError(Exception):
    """A profile file is missing or invalid."""


class TransitionError(Exception):
    """A requested phase transition is not allowed."""


class RoleError(TransitionError):
    """The role does not own that phase."""


@dataclass(frozen=True)
class PhaseDef:
    name: str
    owner: str
    gate: bool = False


@dataclass(frozen=True)
class RoleDef:
    name: str
    runner: str = "cat {brief}"
    coordinator: bool = False


@dataclass
class Profile:
    name: str
    phases: list[PhaseDef] = field(default_factory=list)
    roles: list[RoleDef] = field(default_factory=list)
    interval_seconds: int = 300
    concurrency: int = 1

    @property
    def phase_names(self) -> list[str]:
        return [phase.name for phase in self.phases]

    @property
    def gates(self) -> list[str]:
        return [phase.name for phase in self.phases if phase.gate]

    def phase(self, name: str) -> PhaseDef | None:
        for phase in self.phases:
            if phase.name == name:
                return phase
        return None

    def role(self, name: str | None) -> RoleDef | None:
        if not name:
            return None
        for role in self.roles:
            if role.name == name:
                return role
        return None

    def coordinator_role(self) -> RoleDef | None:
        for role in self.roles:
            if role.coordinator:
                return role
        return None

    def owner_of(self, phase: str) -> str | None:
        definition = self.phase(phase)
        return definition.owner if definition else None


def _read_profile_text(name: str) -> str:
    candidate = Path(name)
    if candidate.suffix == ".toml" or candidate.exists():
        if not candidate.exists():
            raise ProfileError(f"{candidate}: no such profile file")
        return candidate.read_text(encoding="utf-8")
    packaged = PROFILE_DIR / f"{name}.toml"
    if not packaged.exists():
        raise ProfileError(f"unknown profile {name!r}; looked in {PROFILE_DIR}")
    return packaged.read_text(encoding="utf-8")


def load_profile(name: str = STANDARD) -> Profile:
    """Load a profile from a name in the package or an explicit path.

    pipeline.ROSTER.1 — exactly one coordinator role.
    pipeline.ROSTER.2 — every role declares its runner.
    """
    try:
        data = tomllib.loads(_read_profile_text(name))
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{name}: {exc}") from exc

    roster = data.get("roster") or {}
    profile = Profile(
        name=str((data.get("profile") or {}).get("name") or name),
        interval_seconds=int(roster.get("default_interval_seconds", 300)),
        concurrency=int(roster.get("default_concurrency", 1)),
    )
    for entry in data.get("role", []) or []:
        profile.roles.append(
            RoleDef(
                name=str(entry["name"]),
                runner=str(entry.get("runner", "cat {brief}")),
                coordinator=bool(entry.get("coordinator", False)),
            )
        )
    for entry in data.get("phase", []) or []:
        profile.phases.append(
            PhaseDef(
                name=str(entry["name"]),
                owner=str(entry.get("owner", HUMAN)),
                gate=bool(entry.get("gate", False)),
            )
        )
    if not profile.phases:
        raise ProfileError(f"{name}: no phases defined")
    coordinators = [role for role in profile.roles if role.coordinator]
    if len(coordinators) != 1:
        raise ProfileError(
            f"{name}: expected exactly one coordinator role, found {len(coordinators)}"
        )
    return profile


def prior_phases(profile: Profile, phase: str) -> list[str]:
    names = profile.phase_names
    if phase not in names:
        raise TransitionError(f"unknown phase {phase!r}")
    return names[: names.index(phase)]


def set_phase(
    store,
    profile: Profile,
    feature_id: str,
    phase: str,
    target: str,
    *,
    actor: str,
    role: str | None = None,
    channel: str = "cli",
) -> str:
    """Move one phase of one feature. Returns the state now in force."""
    definition = profile.phase(phase)
    if definition is None:
        raise TransitionError(f"unknown phase {phase!r}")

    if target not in {"pending", "started", "blocked", "done"}:
        raise TransitionError(f"unknown state {target!r}")

    # pipeline.PHASE.4 — a role may only move a phase it owns.
    if role and not definition.gate and definition.owner != role:
        raise RoleError(
            f"role {role!r} does not own phase {phase!r} (owner is {definition.owner!r})"
        )

    if target != "done":
        store.set_phase_state(feature_id, phase, target)
        store.event(
            "phase", f"{feature_id}/{phase}", f"{target} by {role or actor} via {channel}"
        )
        return target

    # pipeline.GATE.1 — gates are approvals, not receipts.
    if definition.gate:
        if not store.approved(feature_id, phase):
            raise TransitionError(
                f"{phase!r} is a gate: record an approval instead of a receipt"
            )
    else:
        # pipeline.PHASE.1 / pipeline.RECEIPT.3
        if not store.valid_receipt(feature_id, phase):
            raise TransitionError(
                f"{phase!r} has no passing receipt; run `salt prove` first"
            )

    # pipeline.PHASE.2 — no phase finishes ahead of the one before it.
    for prior in prior_phases(profile, phase):
        if store.phase_state(feature_id, prior) != "done":
            raise TransitionError(
                f"{phase!r} cannot be done while {prior!r} is not done"
            )

    store.set_phase_state(feature_id, phase, "done")
    store.event("phase", f"{feature_id}/{phase}", f"done by {role or actor}")
    return "done"


def approve(
    store,
    profile: Profile,
    feature_id: str,
    gate: str,
    decision: str,
    *,
    note: str = "",
    actor: str = "human",
    channel: str = "cli",
) -> None:
    """Record a human decision on a gate.

    pipeline.GATE.1 — the approval is what makes a gate done, in either direction.
    pipeline.GATE.2 — the channel is recorded.
    """
    definition = profile.phase(gate)
    if definition is None or not definition.gate:
        raise TransitionError(f"{gate!r} is not a gate phase")
    if decision not in {"approved", "rejected"}:
        raise TransitionError(f"unknown decision {decision!r}")

    store.add_approval(feature_id, gate, decision, note, actor, channel)
    if decision == "approved":
        store.set_phase_state(feature_id, gate, "done")
    else:
        store.set_phase_state(feature_id, gate, "blocked")
    store.event(
        "approval", f"{feature_id}/{gate}", f"{decision} by {actor} via {channel}"
    )


def acid_coverage(store, feature_id: str) -> dict[str, int]:
    """How many ACIDs have code and test references."""
    acids = store.acids(feature_id)
    counts = store.ref_counts(feature_id)
    total = len(acids)
    with_code = sum(1 for acid in acids if counts.get(acid["id"], {}).get("code", 0))
    with_tests = sum(1 for acid in acids if counts.get(acid["id"], {}).get("test", 0))
    return {
        "total": total,
        "with_code": with_code,
        "with_tests": with_tests,
        "untested": total - with_tests,
    }
