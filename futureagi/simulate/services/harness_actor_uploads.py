"""Files a person uploads into an environment draft, such as a document an actor may be given to read."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from simulate.models import HostedHarnessJob
from simulate.services.harness_actor_drafts import draft_read, written_cast
from simulate.services.harness_actor_fields import knowledge_of
from simulate.services.harness_actor_options import size_label
from simulate.services.harness_actor_reads import Cast
from simulate.services.harness_actor_rules import (
    UPLOAD_KINDS,
    UPLOAD_MAX_BYTES,
    ActorRecord,
    actor_rules,
    cast_records,
    readable_file,
)
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from accounts.models.user import User

_UPLOAD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,119}$")


def upload_environment_file(
    environment: HostedHarnessJob,
    *,
    kind: str,
    name: str,
    content: bytes,
    user: User | None,
    expected_revision: int | None,
) -> tuple[Cast, str]:
    """Put one uploaded file into the environment draft and return the cast and the stored path.

    Never replaces a file: refuses `upload_exists` (409), `upload_invalid` (400) and `draft_changed` (409)."""
    spec = UPLOAD_KINDS[kind]
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    problems = []
    if not _UPLOAD_NAME.match(base):
        problems.append(
            "name it with letters, digits, spaces, dots, dashes or underscores, up to 120 characters"
        )
    if not base.lower().endswith(spec["formats"]):
        problems.append(f"a {kind} file is one of {', '.join(spec['formats'])}")
    if not content:
        problems.append("the file is empty")
    if len(content) > UPLOAD_MAX_BYTES:
        problems.append(f"the file is larger than {size_label(UPLOAD_MAX_BYTES)}")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        problems.append("the file is not UTF-8 text")
        text = ""
    if text and base.lower().endswith(".json"):
        try:
            json.loads(text)
        except ValueError:
            problems.append("the file is not valid JSON")
    if problems:
        raise HostedHarnessError(
            "upload_invalid",
            f"{base or 'The file'}: " + "; ".join(problems),
            status_code=400,
        )
    refs, environment_files, suite_files = draft_read(environment, expected_revision)
    path = spec["folder"] + base
    if kind == "knowledge" and not readable_file(path, actor_rules(environment_files)):
        raise HostedHarnessError(
            "upload_invalid",
            f"{base}: an actor may not be given a file by this name; rename it",
            status_code=400,
        )
    if path in environment_files:
        raise _taken(base, path, cast_records(environment_files))
    cast = written_cast(
        environment, {**environment_files, path: content}, suite_files, refs, user
    )
    return cast, path


def _taken(base: str, path: str, actors: list[ActorRecord]) -> HostedHarnessError:
    """Why a file of a name the draft holds is not replaced: the actors given it would read the new one unasked."""
    readers = [
        str(one.get("name") or one.get("id") or "")
        for one in actors
        if path in knowledge_of(one)[1]
    ]
    return HostedHarnessError(
        "upload_exists",
        f"This environment already has a file named {base}"
        + (
            f", which {', '.join(readers)} {'reads' if len(readers) == 1 else 'read'}"
            if readers
            else ""
        )
        + "; pick it from the files they can read, or rename this one to upload it as well",
        status_code=409,
        details={"readers": readers},
    )
