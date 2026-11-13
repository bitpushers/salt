"""The HTTP console and the JSON API.

console.BOARD.* — the board, grouped by project.
console.DETAIL.* — one feature in full.
console.QUEUE.* — eligibility, leases, strands.
console.SETTINGS.* — cadence and dispatch controls.
console.ACTIVITY.* — the event feed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import coordinator, pipeline
from . import ingest as ingest_mod
from .config import Config
from .daemon import Daemon
from .store import Store

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def create_app(
    config: Config, store: Store, profile: pipeline.Profile, daemon: Daemon | None = None
) -> FastAPI:
    app = FastAPI(title="Salt", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def profile_for(project: str | None) -> pipeline.Profile:
        if not project:
            return profile
        row = store.project(project)
        name = row["profile"] if row else profile.name
        try:
            return pipeline.load_profile(name)
        except pipeline.ProfileError:
            return profile

    def context(request: Request, page: str, **extra: Any) -> dict:
        return {
            "request": request,
            "page": page,
            "profile": profile,
            "projects": store.projects(),
            "dispatch_enabled": coordinator.dispatch_enabled(store),
            "interval": coordinator.interval_seconds(store, profile),
            **extra,
        }

    def board_rows() -> list[dict]:
        rows = []
        for feature in store.features():
            states = store.phase_states(feature["id"])
            cells = []
            for name in profile.phase_names:
                definition = profile.phase(name)
                row = states.get(name)
                state = row["state"] if row else "pending"
                if definition and definition.gate and state != "done":
                    state = "gate"
                cells.append(
                    {
                        "name": name,
                        "state": state,
                        "owner": definition.owner if definition else "",
                    }
                )
            rows.append(
                {
                    "feature": feature,
                    "cells": cells,
                    "coverage": pipeline.acid_coverage(store, feature["id"]),
                }
            )
        return rows

    def _friendly(ts: str | None) -> str:
        """ISO-8601 in, something a human reads out."""
        if not ts:
            return "never"
        return ts.replace("T", " ").replace("+00:00", " UTC")

    # -- pages ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    # console.BOARD.1
    # console.BOARD.2
    def board(request: Request) -> HTMLResponse:
        """console.BOARD.1 / .2 / .3"""
        return templates.TemplateResponse(
            request, "board.html", context(request, "board", rows=board_rows())
        )

    @app.get("/features/{feature_id:path}", response_class=HTMLResponse)
    # console.DETAIL.1
    # console.DETAIL.2
    # console.DETAIL.3
    def feature_detail(request: Request, feature_id: str) -> HTMLResponse:
        """console.DETAIL.1 / .2 / .3"""
        feature = store.find_feature(feature_id)
        if feature is None:
            raise HTTPException(status_code=404, detail="no such feature")
        acids = store.acids(feature["id"])
        counts = store.ref_counts(feature["id"])
        states = store.phase_states(feature["id"])
        phases = []
        for name in profile.phase_names:
            definition = profile.phase(name)
            row = states.get(name)
            state = row["state"] if row else "pending"
            if definition and definition.gate and state != "done":
                state = "gate"
            phases.append({"name": name, "state": state, "gate": bool(definition and definition.gate)})
        runs = [
            row
            for row in store.runs(200)
            if row["feature_id"] == feature["id"]
        ]
        return templates.TemplateResponse(
            request,
            "feature.html",
            context(
                request,
                "board",
                feature=feature,
                acids=acids,
                counts=counts,
                phases=phases,
                receipts=store.receipts(feature["id"]),
                approvals=store.approvals(feature["id"]),
                runs=runs,
                coverage=pipeline.acid_coverage(store, feature["id"]),
            ),
        )

    @app.get("/roster", response_class=HTMLResponse)
    # console.ROSTER_VIEW.1
    # console.ROSTER_VIEW.2
    def roster(request: Request) -> HTMLResponse:
        """console.ROSTER_VIEW.1 / .2"""
        load = {
            role.name: store.running_count(role.name) for role in profile.roles
        }
        owners = {}
        for phase in profile.phases:
            owners.setdefault(phase.owner, []).append(phase.name)
        return templates.TemplateResponse(
            request,
            "roster.html",
            context(request, "roster", load=load, owners=owners, threshold=coordinator.STRAND_THRESHOLD),
        )

    @app.get("/queue", response_class=HTMLResponse)
    # console.QUEUE.1
    # console.QUEUE.2
    # console.QUEUE.3
    def queue(request: Request) -> HTMLResponse:
        """console.QUEUE.1 / .2 / .3"""
        return templates.TemplateResponse(
            request,
            "queue.html",
            context(
                request,
                "queue",
                eligible=coordinator.eligible(store, profile),
                waiting=coordinator.waiting(store, profile),
                claims=store.live_claims(),
                stranded=coordinator.stranded(store, profile),
            ),
        )

    @app.get("/coordinator", response_class=HTMLResponse)
    # console.SETTINGS.1
    def coordinator_page(request: Request) -> HTMLResponse:
        """console.SETTINGS.1"""
        return templates.TemplateResponse(
            request,
            "coordinator.html",
            context(
                request,
                "coordinator",
                last_tick=store.get_setting(coordinator.SETTING_LAST_TICK),
                last_tick_friendly=_friendly(store.get_setting(coordinator.SETTING_LAST_TICK)),
                saved=request.query_params.get("saved") == "1",
                concurrency=coordinator.concurrency(store, profile),
                runs=store.runs(20),
                result=None,
            ),
        )

    @app.post("/coordinator/settings")
    # console.SETTINGS.2
    # console.SETTINGS.3
    def save_settings(
        interval: int = Form(...), concurrency: int = Form(1), enabled: str = Form("off")
    ) -> RedirectResponse:
        """console.SETTINGS.2 / .3"""
        coordinator.set_interval(store, interval)
        coordinator.set_concurrency(store, concurrency)
        coordinator.set_dispatch_enabled(store, enabled == "on")
        store.event(
            "setting",
            "coordinator",
            f"interval={interval}s concurrency={concurrency} enabled={enabled == 'on'}",
        )
        return RedirectResponse(url="/coordinator?saved=1", status_code=303)

    @app.post("/coordinator/run-now", response_class=HTMLResponse)
    # console.SETTINGS.4
    def run_now(request: Request) -> HTMLResponse:
        """console.SETTINGS.4"""
        result = daemon.tick() if daemon else {"error": "no coordinator in this process"}
        return templates.TemplateResponse(
            request,
            "coordinator.html",
            context(
                request,
                "coordinator",
                last_tick=store.get_setting(coordinator.SETTING_LAST_TICK),
                last_tick_friendly=_friendly(store.get_setting(coordinator.SETTING_LAST_TICK)),
                saved=False,
                concurrency=coordinator.concurrency(store, profile),
                runs=store.runs(20),
                result=result,
            ),
        )

    @app.post("/features/{feature_id:path}/approve")
    def approve_from_web(
        feature_id: str,
        gate: str = Form(...),
        decision: str = Form("approved"),
        note: str = Form(""),
    ) -> RedirectResponse:
        """pipeline.GATE.2 — the channel is recorded as web."""
        feature = store.find_feature(feature_id)
        if feature is None:
            raise HTTPException(status_code=404, detail="no such feature")
        pipeline.approve(
            store,
            profile_for(feature["project"]),
            feature["id"],
            gate,
            decision,
            note=note,
            actor="human",
            channel="web",
        )
        return RedirectResponse(url=f"/features/{feature['id']}", status_code=303)

    @app.get("/activity", response_class=HTMLResponse)
    # console.ACTIVITY.1
    # console.ACTIVITY.2
    def activity(request: Request) -> HTMLResponse:
        """console.ACTIVITY.1 / .2"""
        return templates.TemplateResponse(
            request, "activity.html", context(request, "activity", events=store.events(300))
        )

    # -- JSON API ---------------------------------------------------------
    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "projects": len(store.projects())}

    @app.get("/api/projects")
    def api_projects() -> dict:
        return {
            "projects": [
                {"name": row["name"], "repo_path": row["repo_path"], "profile": row["profile"]}
                for row in store.projects()
            ]
        }

    @app.get("/api/features")
    def api_features(project: str | None = None, phase: str | None = None) -> dict:
        out = []
        for feature in store.features(project):
            states = store.phase_states(feature["id"])
            current = None
            for name in profile.phase_names:
                state = states[name]["state"] if name in states else "pending"
                if state != "done":
                    current = name
                    break
            if phase and current != phase:
                continue
            out.append(
                {
                    "id": feature["id"],
                    "project": feature["project"],
                    "name": feature["name"],
                    "draft": bool(feature["draft"]),
                    "phase": current,
                    "coverage": pipeline.acid_coverage(store, feature["id"]),
                }
            )
        return {"features": out}

    @app.get("/api/features/{feature_id:path}")
    def api_feature(feature_id: str) -> dict:
        feature = store.find_feature(feature_id)
        if feature is None:
            raise HTTPException(status_code=404, detail="no such feature")
        counts = store.ref_counts(feature["id"])
        return {
            "id": feature["id"],
            "name": feature["name"],
            "project": feature["project"],
            "spec": feature["file_path"],
            "content_hash": feature["content_hash"],
            "acids": [
                {
                    "id": acid["id"],
                    "group": acid["grp"],
                    "num": acid["num"],
                    "text": acid["text"],
                    "state": acid["state"],
                    "refs": counts.get(acid["id"], {}),
                }
                for acid in store.acids(feature["id"])
            ],
            "phases": {
                name: (row["state"] if row else "pending")
                for name, row in store.phase_states(feature["id"]).items()
            },
            "receipts": [dict(row) for row in store.receipts(feature["id"])],
            "approvals": [dict(row) for row in store.approvals(feature["id"])],
        }

    @app.get("/api/roles")
    def api_roles() -> dict:
        return {
            "roles": [
                {
                    "name": role.name,
                    "runner": role.runner,
                    "coordinator": role.coordinator,
                    "running": store.running_count(role.name),
                }
                for role in profile.roles
            ],
            "phases": [
                {"name": phase.name, "owner": phase.owner, "gate": phase.gate}
                for phase in profile.phases
            ],
        }

    @app.get("/api/next")
    def api_next(as_role: str | None = None) -> dict:
        units = coordinator.eligible(store, profile)
        if as_role:
            units = [unit for unit in units if unit.role == as_role]
        return {
            "units": [
                {
                    "feature_id": unit.feature_id,
                    "project": unit.project,
                    "phase": unit.phase,
                    "role": unit.role,
                }
                for unit in units
            ]
        }

    @app.post("/api/claims")
    def api_claim(payload: dict) -> JSONResponse:
        feature = store.find_feature(str(payload.get("feature", "")))
        if feature is None:
            raise HTTPException(status_code=404, detail="no such feature")
        phase = str(payload.get("phase", ""))
        role = payload.get("role")
        actor = str(payload.get("actor", "api"))
        definition = profile.phase(phase)
        if definition is None:
            raise HTTPException(status_code=422, detail="unknown phase")
        if role and definition.owner != role:
            raise HTTPException(status_code=403, detail="role does not own that phase")
        unit = coordinator.Unit(
            feature_id=feature["id"],
            project=feature["project"],
            feature_name=feature["name"],
            phase=phase,
            role=definition.owner,
        )
        try:
            claim_id = coordinator.claim(
                store, profile, unit, actor=actor, ttl_seconds=int(payload.get("ttl", 1800))
            )
        except pipeline.TransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"claim": claim_id}, status_code=201)

    @app.delete("/api/claims/{claim_id}")
    def api_release(claim_id: int) -> dict:
        store.release_claim(claim_id)
        store.event("claim", f"claim:{claim_id}", "released")
        return {"released": claim_id}

    @app.post("/api/receipts")
    def api_receipt(payload: dict) -> JSONResponse:
        """pipeline.RECEIPT.3 — a non-zero exit is refused, not recorded as done."""
        feature = store.find_feature(str(payload.get("feature", "")))
        if feature is None:
            raise HTTPException(status_code=404, detail="no such feature")
        phase = str(payload.get("phase", ""))
        exit_code = int(payload.get("exit_code", 1))
        if exit_code != 0:
            raise HTTPException(
                status_code=409, detail="a non-zero exit cannot satisfy a phase"
            )
        store.add_receipt(
            feature["id"],
            phase,
            str(payload.get("command", "")),
            payload.get("cwd"),
            exit_code,
            payload.get("sha"),
            payload.get("digest"),
            payload.get("output_tail"),
            str(payload.get("actor", "api")),
            payload.get("role"),
        )
        store.event("receipt", f"{feature['id']}/{phase}", f"exit 0 from {payload.get('actor', 'api')}")
        return JSONResponse({"recorded": True}, status_code=201)

    @app.post("/api/approvals")
    def api_approval(payload: dict) -> dict:
        feature = store.find_feature(str(payload.get("feature", "")))
        if feature is None:
            raise HTTPException(status_code=404, detail="no such feature")
        gate = str(payload.get("gate", ""))
        definition = profile.phase(gate)
        if definition is None or not definition.gate:
            raise HTTPException(status_code=422, detail="not a gate phase")
        pipeline.approve(
            store,
            profile,
            feature["id"],
            gate,
            str(payload.get("decision", "approved")),
            note=str(payload.get("note", "")),
            actor=str(payload.get("actor", "api")),
            channel=str(payload.get("channel", "cli")),
        )
        return {"recorded": True}

    @app.post("/api/ingest")
    def api_ingest(payload: dict) -> dict:
        project = str(payload.get("project", ""))
        row = store.project(project)
        if row is None:
            raise HTTPException(status_code=404, detail="no such project")
        return ingest_mod.ingest_repository(store, profile, project, row["repo_path"])

    @app.get("/api/runs")
    def api_runs() -> dict:
        return {"runs": [dict(row) for row in store.runs(50)]}

    @app.get("/api/events")
    def api_events(since: str | None = None) -> dict:
        return {"events": [dict(row) for row in store.events(200, since)]}

    @app.get("/api/coordinator")
    def api_coordinator() -> dict:
        return {
            "interval_seconds": coordinator.interval_seconds(store, profile),
            "concurrency": coordinator.concurrency(store, profile),
            "enabled": coordinator.dispatch_enabled(store),
            "last_tick": store.get_setting(coordinator.SETTING_LAST_TICK),
        }

    @app.put("/api/coordinator")
    def api_coordinator_set(payload: dict) -> dict:
        if "interval_seconds" in payload:
            coordinator.set_interval(store, int(payload["interval_seconds"]))
        if "concurrency" in payload:
            coordinator.set_concurrency(store, int(payload["concurrency"]))
        if "enabled" in payload:
            coordinator.set_dispatch_enabled(store, bool(payload["enabled"]))
        return api_coordinator()

    @app.post("/api/coordinator/run-now")
    def api_run_now() -> dict:
        if daemon is None:
            raise HTTPException(status_code=503, detail="no coordinator in this process")
        return daemon.tick()

    return app
