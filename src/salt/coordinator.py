"""Eligibility and leases.

pipeline.CLAIM.* — one live claim per unit, with an expiry, so a crashed bot cannot
strand a feature forever.
pipeline.GATE.3 — a phase behind an unsatisfied gate is never eligible for dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import pipeline
from .store import Store

STRAND_THRESHOLD = 3
DEFAULT_TTL_SECONDS = 1800

SETTING_INTERVAL = "coordinator.interval_seconds"
SETTING_CONCURRENCY = "coordinator.concurrency"
SETTING_ENABLED = "coordinator.enabled"
SETTING_LAST_TICK = "coordinator.last_tick"


@dataclass
class Unit:
    feature_id: str
    project: str
    feature_name: str
    phase: str
    role: str | None
    reason: str = ""


def interval_seconds(store: Store, profile: pipeline.Profile) -> int:
    raw = store.get_setting(SETTING_INTERVAL)
    if raw is None:
        return profile.interval_seconds
    try:
        return max(15, int(raw))
    except ValueError:
        return profile.interval_seconds


def set_interval(store: Store, seconds: int) -> None:
    store.set_setting(SETTING_INTERVAL, str(max(15, int(seconds))))


def concurrency(store: Store, profile: pipeline.Profile) -> int:
    raw = store.get_setting(SETTING_CONCURRENCY)
    if raw is None:
        return profile.concurrency
    try:
        return max(1, int(raw))
    except ValueError:
        return profile.concurrency


def set_concurrency(store: Store, value: int) -> None:
    store.set_setting(SETTING_CONCURRENCY, str(max(1, int(value))))


def dispatch_enabled(store: Store) -> bool:
    """Dispatch starts off. Enabling it is a deliberate act."""
    return (store.get_setting(SETTING_ENABLED, "0") or "0") == "1"


def set_dispatch_enabled(store: Store, enabled: bool) -> None:
    store.set_setting(SETTING_ENABLED, "1" if enabled else "0")


def _candidate(store: Store, profile: pipeline.Profile, feature_row) -> Unit | None:
    """The first phase of a feature that is not done, if it is dispatchable."""
    feature_id = feature_row["id"]
    states = store.phase_states(feature_id)
    for name in profile.phase_names:
        definition = profile.phase(name)
        if definition is None:
            continue
        state = states[name]["state"] if name in states else "pending"
        if state == "done":
            continue
        unit = Unit(
            feature_id=feature_id,
            project=feature_row["project"],
            feature_name=feature_row["name"],
            phase=name,
            role=None if definition.gate else definition.owner,
        )
        if definition.gate:
            unit.reason = "waiting on a human gate" if not store.approved(feature_id, name) else ""
            return unit
        if state == "blocked":
            unit.reason = "blocked"
            return unit
        if store.live_claim(feature_id, name):
            unit.reason = "claimed"
            return unit
        return unit
    return None


def eligible(store: Store, profile: pipeline.Profile) -> list[Unit]:
    """Units the coordinator may dispatch right now."""
    units: list[Unit] = []
    for feature_row in store.features():
        unit = _candidate(store, profile, feature_row)
        if unit and unit.role and not unit.reason:
            units.append(unit)
    return units


def waiting(store: Store, profile: pipeline.Profile) -> list[Unit]:
    """Units that need a human, or that are blocked or claimed."""
    units: list[Unit] = []
    for feature_row in store.features():
        unit = _candidate(store, profile, feature_row)
        if unit and (unit.reason or unit.role is None):
            unit.reason = unit.reason or "waiting on a human gate"
            units.append(unit)
    return units


def stranded(store: Store, profile: pipeline.Profile, threshold: int = STRAND_THRESHOLD) -> list[Unit]:
    """Units whose lease lapsed repeatedly, or that are blocked."""
    units: list[Unit] = []
    for feature_row in store.features():
        for row in store.phase_states(feature_row["id"]).values():
            if row["state"] == "done":
                continue
            if row["state"] == "blocked" or int(row["strands"]) >= threshold:
                units.append(
                    Unit(
                        feature_id=feature_row["id"],
                        project=feature_row["project"],
                        feature_name=feature_row["name"],
                        phase=row["name"],
                        role=profile.owner_of(row["name"]),
                        reason=f"{row['strands']} strand(s)" if row["strands"] else "blocked",
                    )
                )
    return units


def claim(
    store: Store,
    profile: pipeline.Profile,
    unit: Unit,
    *,
    actor: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> int:
    """Take a lease on a unit. pipeline.CLAIM.2 — one live claim per unit."""
    definition = profile.phase(unit.phase)
    if definition is None:
        raise pipeline.TransitionError(f"unknown phase {unit.phase!r}")
    if definition.gate:
        raise pipeline.TransitionError(f"{unit.phase!r} is a gate; it cannot be claimed")
    if store.live_claim(unit.feature_id, unit.phase):
        raise pipeline.TransitionError(
            f"{unit.feature_id}/{unit.phase} is already claimed"
        )
    claim_id = store.add_claim(
        unit.feature_id, unit.phase, definition.owner, actor, ttl_seconds
    )
    store.set_phase_state(unit.feature_id, unit.phase, "started")
    store.event(
        "claim",
        f"{unit.feature_id}/{unit.phase}",
        f"{definition.owner} leased for {ttl_seconds}s to {actor}",
    )
    return claim_id


def expire(store: Store) -> list[dict]:
    """Release lapsed leases and count a strand against each unit."""
    expired: list[dict] = []
    for row in store.expire_claims():
        strands = store.add_strand(row["feature_id"], row["phase"])
        store.event(
            "escalation",
            f"{row['feature_id']}/{row['phase']}",
            f"lease expired after {strands} strand(s)",
        )
        expired.append(
            {
                "feature_id": row["feature_id"],
                "phase": row["phase"],
                "strands": strands,
            }
        )
    return expired
