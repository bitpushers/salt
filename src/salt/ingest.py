"""Scanning a repository into the database.

ingest.INGEST.* — one feature per spec file, ACIDs upserted, refs replaced, features
removed when their spec file disappears.
"""

from __future__ import annotations

from pathlib import Path

from . import pipeline, scanner
from .store import Store


def ingest_repository(
    store: Store, profile: pipeline.Profile, project_name: str, repo_path: str | Path
) -> dict:
    repo = Path(repo_path).expanduser()
    if not repo.is_dir():
        raise FileNotFoundError(f"{repo}: not a directory")
    repo = repo.resolve()

    store.upsert_project(project_name, str(repo), profile.name)

    parsed = []
    keep: list[str] = []
    for path in scanner.spec_paths(repo):
        spec = scanner.parse_spec(path)
        parsed.append((path, spec))
        feature_id = f"{project_name}:{spec.name}"
        keep.append(feature_id)
        store.upsert_feature(
            feature_id,
            project_name,
            spec.name,
            str(path.relative_to(repo)),
            spec.draft,
            spec.description,
            spec.content_hash,
        )
        store.ensure_phases(feature_id, profile.phase_names)
        for acid in spec.acids:
            store.upsert_acid(
                acid.acid_id(spec.name), feature_id, acid.group, acid.num, acid.text
            )
        store.prune_acids(feature_id, spec.acid_ids)

    removed = store.delete_features_not_in(project_name, keep)

    known = {acid_id for _, spec in parsed for acid_id in spec.acid_ids}
    refs = scanner.scan_refs(repo, known)
    store.replace_refs(project_name, refs)
    store.event(
        "ingest",
        project_name,
        f"{len(parsed)} feature(s), {len(known)} ACID(s), {len(refs)} reference(s)",
    )
    return {
        "project": project_name,
        "repo": str(repo),
        "features": len(parsed),
        "acids": len(known),
        "refs": len(refs),
        "removed": removed,
    }
