"""The role a person gives each file attached to an environment: a policy or reference, seed records, or material
only the evaluator reads. Kept in the draft as `knowledge/roles.json`, which the harness seals: an evaluator-only
file is kept from the agent and from every actor."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from simulate.models import HostedHarnessJob
from simulate.services.harness_actor_drafts import draft_read, written_cast
from simulate.services.harness_actor_fields import knowledge_of
from simulate.services.harness_actor_reads import Cast
from simulate.services.harness_actor_rules import (
    KNOWLEDGE_ROLES,
    FileRole,
    cast_records,
    file_roles,
)
from simulate.services.harness_seed_joins import seed_records_problem
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from accounts.models.user import User


def with_role(
    environment_files: dict[str, bytes], path: str, role: FileRole
) -> dict[str, bytes]:
    """The environment's files with ``path`` given ``role`` in `knowledge/roles.json`."""
    roles = {**file_roles(environment_files), path: role}
    body = json.dumps(dict(sorted(roles.items())), indent=2) + "\n"
    return {**environment_files, KNOWLEDGE_ROLES: body.encode("utf-8")}


def set_file_role(
    environment: HostedHarnessJob,
    *,
    path: str,
    role: FileRole,
    user: User | None,
    expected_revision: int | None,
) -> Cast:
    """Give one attached file its role in the environment draft and return the cast.

    Refuses `file_not_found` (404) for a path the draft does not attach, `seed_records_invalid` (400) for seed records
    that cannot be read as rows, `file_in_use` (409) when making a file evaluator-only that an actor is given to read,
    and `draft_changed` (409)."""
    refs, environment_files, suite_files = draft_read(environment, expected_revision)
    if (
        not path.startswith("knowledge/")
        or path == KNOWLEDGE_ROLES
        or path not in environment_files
    ):
        raise HostedHarnessError(
            "file_not_found",
            "This environment has no attached file at that path",
            status_code=404,
        )
    if role == "seed_records" and (
        problem := seed_records_problem(path, environment_files[path])
    ):
        raise HostedHarnessError(
            "seed_records_invalid",
            f"{path.rsplit('/', 1)[-1]} cannot be read as seed records: {problem}",
            status_code=400,
        )
    if role == "evaluator_only":
        readers = [
            str(one.get("name") or one.get("id") or "")
            for one in cast_records(environment_files)
            if path in knowledge_of(one)[1]
        ]
        if readers:
            raise HostedHarnessError(
                "file_in_use",
                f"{', '.join(readers)} {'reads' if len(readers) == 1 else 'read'} this file; take it from "
                f"{'them' if len(readers) > 1 else 'that actor'} first, then keep it for the evaluator only",
                status_code=409,
                details={"readers": readers},
            )
    return written_cast(
        environment,
        with_role(environment_files, path, role),
        suite_files,
        refs,
        user,
    )
