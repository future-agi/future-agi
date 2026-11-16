"""Where an environment's cast is kept: its draft, read at the revision the editor read, and written back with the
cast beside every other top-level key the actors file holds."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from django.db import transaction

from simulate.models import HostedHarnessJob
from simulate.services.harness_actor_reads import Cast, cast_view
from simulate.services.harness_actor_rules import (
    ACTORS_FILE,
    ActorRecord,
    ActorRules,
    UnresolvedPoint,
    read_json,
    stored_limits,
    unresolved_points,
    world_stores,
)
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.drafts import DraftRef
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from accounts.models.user import User


def with_cast(
    environment_files: dict[str, bytes],
    actors: list[ActorRecord],
    rules: ActorRules,
    *,
    limits: dict[str, Any] | None = None,
    unresolved: list[UnresolvedPoint] | None = None,
) -> dict[str, bytes]:
    """The environment's files with this cast, keeping its limits and unresolved points (or these new ones), and every
    other top-level key the stored file holds (a later release's, such as `actor_share`) as it is."""
    kept = stored_limits(environment_files) if limits is None else limits
    points = unresolved_points(environment_files) if unresolved is None else unresolved
    stored = read_json(environment_files, ACTORS_FILE)
    others = {
        key: value
        for key, value in (stored if isinstance(stored, dict) else {}).items()
        if key not in _OWNED_KEYS
    }
    files = dict(environment_files)
    # No actors, no limits and nothing else kept means no file, so removing the only actor leaves nothing to review.
    files.pop(ACTORS_FILE, None)
    if actors or kept or others:
        body: dict[str, Any] = {
            "schema": rules["formats"][ACTORS_FILE],
            "actors": actors,
        }
        if kept:
            body["limits"] = kept
        if points:
            body["unresolved"] = points
        files[ACTORS_FILE] = (json.dumps({**body, **others}, indent=2) + "\n").encode(
            "utf-8"
        )
    return files


# The top-level keys of the actors file this writer builds; any other is a release's own and kept as stored.
_OWNED_KEYS = ("schema", "actors", "limits", "unresolved")


def draft_read(
    environment: HostedHarnessJob, expected_revision: int | None
) -> tuple[dict[str, DraftRef], dict[str, bytes], dict[str, bytes]]:
    """Both pieces' draft refs and files, refusing `draft_changed` (409) when the caller read an older revision. The
    refs are read before the files, so an edit landing on either piece in between fails the write's check of both."""
    refs = drafts.draft_refs(environment)
    sides = drafts.edit_sides(environment)
    if (
        expected_revision is not None
        and expected_revision != refs["environment"].revision
    ):
        raise HostedHarnessError(
            "draft_changed",
            "the actors were edited elsewhere since you read them; read them again",
            status_code=409,
        )
    return refs, sides["environment"], sides["suite"]


def written_cast(
    environment: HostedHarnessJob,
    environment_files: dict[str, bytes],
    suite_files: dict[str, bytes],
    refs: dict[str, DraftRef],
    user: User | None,
) -> Cast:
    """Record the environment side of the draft and return the cast as it now reads. Both pieces' refs are checked:
    a scenario saved meanwhile may name an actor this edit removes."""
    try:
        written = drafts.record_draft(
            environment,
            archives.pack_archive(archives.compose(environment_files, suite_files)),
            origin="manual",
            user=user,
            expected=dict(refs),
            pieces={"environment"},
            # Validated against the scenarios: a scenario edit landing meanwhile refuses this one.
            checked=set(drafts.DRAFT_PIECES),
        )
    except HostedHarnessError as exc:
        if exc.code != "draft_changed":
            raise
        raise HostedHarnessError(
            "draft_changed",
            "the actors or scenarios were edited elsewhere since you read them; read them again",
            status_code=409,
        ) from exc
    if written is None:
        raise HostedHarnessError(
            "drafts_unavailable",
            "this environment does not keep drafts",
            status_code=409,
        )
    transaction.on_commit(lambda: drafts.retire_chat_runtime(environment))
    draft = written.environment
    return cast_view(
        environment_files,
        suite_files,
        draft.revision if draft is not None else None,
        world_stores(environment),
    )
