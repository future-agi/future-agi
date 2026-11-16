"""Files a person uploads into an environment draft, such as a document an actor may be given to read."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from simulate.models import HostedHarnessJob
from simulate.services.harness_actor_reads import Cast
from simulate.services.harness_actor_rules import actor_rules, readable_file
from simulate.services.harness_actors import draft_read, written_cast
from simulate.services.hosted_harness import HostedHarnessError

if TYPE_CHECKING:
    from accounts.models.user import User

# What a person may upload into an environment draft, by kind: the folder it lands in and the formats it may be. A
# later kind (seed data, recordings) adds one entry here and a reader where it is used, nothing else.
UPLOAD_KINDS: dict[str, dict[str, Any]] = {
    # Documents an actor may be given to read (a policy, a price list), picked per actor like any environment file.
    "knowledge": {"folder": "knowledge/", "formats": (".md", ".txt", ".json", ".csv")},
}


# An upload's size bound: an actor is given at most the start of a file, so a larger one only costs storage.
UPLOAD_MAX_BYTES = 1024 * 1024


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
    """Put one uploaded file into the environment draft under its kind's folder and return the cast, whose knowledge
    sources then list it, and the path the file was stored at. A file of the same name is replaced. Refuses
    `upload_kind_unknown`, `upload_invalid` (400) and `draft_changed` (409)."""
    spec = UPLOAD_KINDS.get(kind)
    if spec is None:
        raise HostedHarnessError(
            "upload_kind_unknown",
            f"{kind!r} is not a kind of upload; one of {', '.join(sorted(UPLOAD_KINDS))}",
            status_code=400,
        )
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
        problems.append(f"the file is larger than {UPLOAD_MAX_BYTES // 1024} KB")
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
    cast = written_cast(
        environment, {**environment_files, path: content}, suite_files, refs, user
    )
    return cast, path
