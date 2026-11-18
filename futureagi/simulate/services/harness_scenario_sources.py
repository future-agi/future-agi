"""Where each requirement of a scenario comes from, resolved against the versions in view.

A source is read from the member's document (`content["sources"]`), never from a proof's copy.
Rules resolve by quote against the version's contract; a file resolves once, when proofs are
recorded or the sources are edited, and the stored digest is compared with the version's file
digests on every read, so a list call never reads an archive.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any, TypedDict

import structlog

logger = structlog.get_logger(__name__)

SOURCE_KINDS = (
    "rule",
    "file",
    "chat_message",
    "evidence_case",
    "requirement",
    "assumption",
)
QUOTE_MAX = 500
FILE_READ_MAX = 1024 * 1024
FILE_CHANGED = "the file changed; validate again"
FILE_UNCHECKED = "not checked yet; validate to check the quote"


class SourceView(TypedDict):
    kind: str
    ref: str
    quote: str
    supports: str
    resolved: bool
    label: str
    link: str | None
    problem: str


def normalise(text: object) -> str:
    """The same normalisation ALK uses for quotes: casefold, no punctuation, single spaces."""
    text = str(text or "").casefold().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^\w\s.]", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def bare_digest(value: object) -> str:
    return str(value or "").removeprefix("sha256:")


NO_SOURCES = "Cite where this case comes from, or add an assumption source that says what you assumed."
_CONTROL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def source_problems(value: object, *, require_one: bool = False) -> list[str]:
    """Why a `sources` value is not a list of sources, one sentence per bad entry: `sources[i]: ...`.
    ``require_one`` refuses an empty list: an edit must cite something or label an assumption."""
    if not isinstance(value, list):
        return ["sources: must be a list"]
    if require_one and not value:
        return [NO_SOURCES]
    problems = []
    for at, one in enumerate(value):
        if not isinstance(one, Mapping):
            problems.append(f"sources[{at}]: must be an object")
            continue
        kind = one.get("kind")
        if kind not in SOURCE_KINDS:
            problems.append(
                f"sources[{at}]: kind must be one of {', '.join(SOURCE_KINDS)}"
            )
            continue
        for field in ("ref", "quote", "supports"):
            if field in one and not isinstance(one[field], str):
                problems.append(f"sources[{at}]: {field} must be a string")
        quote = one.get("quote") or ""
        if isinstance(quote, str) and len(quote) > QUOTE_MAX:
            problems.append(
                f"sources[{at}]: quote is longer than {QUOTE_MAX} characters"
            )
        if kind in ("rule", "file") and not str(quote or "").strip():
            problems.append(
                f"sources[{at}]: a {kind} source needs the exact quote it cites"
            )
        if kind == "assumption" and not (
            str(quote or "").strip() or str(one.get("supports") or "").strip()
        ):
            problems.append(
                f"sources[{at}]: an assumption source says what was assumed"
            )
    return problems


REQUESTED_LINE = re.compile(r"^Requested case \d+: (.+)$", re.MULTILINE)
REQUESTED_MESSAGES = 20


def requested_uncovered(environment: Any) -> list[str]:
    """The lines a person asked for in the builder chat ("Requested case N: ...", the last 20
    messages) that no scenario of the suite being edited cites by quote yet."""
    from simulate.models import HarnessSuiteMember, HostedHarnessConversationMessage
    from simulate.services.harness_versioning.drafts import open_draft

    messages = list(
        HostedHarnessConversationMessage.no_workspace_objects.filter(
            conversation__job_id=environment.id, role="user"
        )
        .order_by("-sequence")
        .values_list("content", flat=True)[:REQUESTED_MESSAGES]
    )
    lines: list[str] = []
    for content in reversed(messages):
        for line in REQUESTED_LINE.findall(content or ""):
            if line.strip() and line.strip() not in lines:
                lines.append(line.strip())
    if not lines:
        return []
    suite = open_draft(environment, "suite") or environment.suite_version
    quotes = {
        normalise(source.get("quote"))
        for content in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=suite
        ).values_list("content", flat=True)
        for source in (content or {}).get("sources") or []
        if isinstance(source, Mapping) and source.get("quote")
    }
    return [line for line in lines if normalise(line) not in quotes]


SEED_STATE = "state.json"
SEED_ROWS_MAX = 50


def seed_records(environment: Any, table: str) -> dict[str, Any]:
    """Up to 50 rows of one table as the default environment version's seeded world holds them,
    read from its stored archive (never the live database). `available` is false for a world whose
    records live only in the agent's own service."""
    import json

    version = getattr(environment, "environment_version", None)
    body = (
        file_bodies(version, {SEED_STATE}).get(SEED_STATE)
        if version is not None
        else None
    )
    if body is None:
        return {"table": table, "rows": [], "available": False}
    try:
        state = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"table": table, "rows": [], "available": False}
    tables = (
        state.get("tables")
        if isinstance(state, Mapping) and isinstance(state.get("tables"), Mapping)
        else state
    )
    found = tables.get(table) if isinstance(tables, Mapping) else None
    if isinstance(found, Mapping):
        rows = [
            {"id": str(key), **value}
            if isinstance(value, Mapping)
            else {"id": str(key), "value": value}
            for key, value in found.items()
        ]
    elif isinstance(found, list):
        rows = [one if isinstance(one, Mapping) else {"value": one} for one in found]
    else:
        rows = []
    return {"table": table, "rows": rows[:SEED_ROWS_MAX], "available": True}


def _sub_goal_names(sub_goals: object) -> set[str]:
    return {
        str(one.get("name") if isinstance(one, Mapping) else one)
        for one in sub_goals or []
        if (one.get("name") if isinstance(one, Mapping) else one)
    }


def negative_control_problems(value: object, sub_goals: object) -> list[str]:
    """Why a `negative_controls` value is not a list of controls this case's checks can fail,
    one sentence per problem: `negative_controls[i]: ...`."""
    if not isinstance(value, list):
        return ["negative_controls: must be a list"]
    names = _sub_goal_names(sub_goals)
    problems: list[str] = []
    seen: set[str] = set()
    for at, one in enumerate(value):
        where = f"negative_controls[{at}]"
        if not isinstance(one, Mapping):
            problems.append(f"{where}: must be an object")
            continue
        name = one.get("name")
        if not isinstance(name, str) or not _CONTROL_NAME.match(name):
            problems.append(f"{where}: name must be a lowercase slug")
        elif name in seen:
            problems.append(f"{where}: another control is already named {name}")
        else:
            seen.add(name)
        if not isinstance(one.get("why"), str) or not one["why"].strip():
            problems.append(f"{where}: why must say what the wrong behaviour is")
        steps = one.get("steps")
        if not isinstance(steps, list) or not steps:
            problems.append(f"{where}: steps must be a list of tool calls")
        else:
            for number, step in enumerate(steps):
                if (
                    not isinstance(step, Mapping)
                    or not isinstance(step.get("tool"), str)
                    or not step["tool"].strip()
                    or not isinstance(
                        step.get("args", step.get("arguments", {})), Mapping
                    )
                ):
                    problems.append(
                        f"{where}: steps[{number}] must be {{tool, args}} with a tool name"
                    )
        must_fail = one.get("must_fail") or []
        if not isinstance(must_fail, list):
            problems.append(f"{where}: must_fail must be a list of sub-goal names")
        else:
            unknown = [str(goal) for goal in must_fail if str(goal) not in names]
            if unknown:
                problems.append(
                    f"{where}: must_fail names {', '.join(unknown)}, which this case does not check"
                )
    return problems


FILE_READ_LIMIT = 1024 * 1024


def file_bodies(version: Any, paths: set[str]) -> dict[str, bytes]:
    """Each named file of a version's archive, read once (also under ``knowledge/``); a path the
    archive does not hold, or an unreadable archive, is left out."""
    from simulate.services.harness_versioning import archives

    if not paths or not getattr(version, "archive_object_key", None):
        return {}
    wanted = paths | {f"knowledge/{path}" for path in paths}
    try:
        members = archives.stream_members(
            version.archive_object_key, wanted, FILE_READ_LIMIT
        )
    except Exception:  # noqa: BLE001 - an unreadable archive reads as no files
        logger.warning(
            "harness_source_files_unread", version_id=str(getattr(version, "id", ""))
        )
        return {}
    found: dict[str, bytes] = {}
    for path in paths:
        body = members.get(path) or members.get(f"knowledge/{path}")
        if body is not None:
            found[path] = body
    return found


def _rule_match(quote: str, rules: list[str]) -> int | None:
    wanted = normalise(quote)
    return next(
        (at for at, rule in enumerate(rules) if wanted and wanted in normalise(rule)),
        None,
    )


def file_path(ref: str) -> str:
    """The attached file a file source cites, as its path in the environment."""
    return ref.removeprefix("file:").strip().lstrip("/")


def _rules(environment_version: Any) -> list[str]:
    contract = (getattr(environment_version, "documents", None) or {}).get("contract")
    rules = contract.get("hard_constraints") if isinstance(contract, Mapping) else None
    return [str(one) for one in rules or []]


def _file_digests(environment_version: Any) -> dict[str, str]:
    digests = (getattr(environment_version, "documents", None) or {}).get(
        "file_digests"
    )
    return dict(digests) if isinstance(digests, Mapping) else {}


def _digest_for(path: str, digests: Mapping[str, str]) -> str | None:
    for candidate in (path, f"knowledge/{path}"):
        if candidate in digests:
            return bare_digest(digests[candidate])
    return None


def resolve_file_text(path: str, text: str, quote: str, digest: str) -> dict[str, Any]:
    """The stored result for one file source: whether the quote is in the file, with its digest."""
    return {
        "ref": f"file:{path}",
        "resolved": normalise(quote) in normalise(text),
        "matched_digest": digest,
    }


def stored_file_results(
    sources: Iterable[Mapping[str, Any]],
    *,
    archive_key: str | None,
    proof_sources: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Resolve each `file` source once: from ALK's own result when it carries a digest, else by
    reading the file from the environment archive. Never raises; an unreadable file is unresolved."""
    from simulate.services.harness_versioning import archives

    by_ref = {
        str(one.get("ref")): one
        for one in proof_sources
        if isinstance(one, Mapping)
        and one.get("kind") == "file"
        and one.get("matched_digest")
    }
    results: list[dict[str, Any]] = []
    wanted = [
        one for one in sources if isinstance(one, Mapping) and one.get("kind") == "file"
    ]
    unread = [one for one in wanted if str(one.get("ref")) not in by_ref]
    texts: dict[str, bytes] = {}
    if unread and archive_key:
        paths = {file_path(str(one.get("ref") or "")) for one in unread}
        paths |= {f"knowledge/{path}" for path in paths}
        try:
            texts = archives.stream_members(archive_key, paths, FILE_READ_MAX)
        except Exception:  # noqa: BLE001 - an unreadable archive leaves the source unresolved
            logger.warning("harness_scenario_source_file_unread", archive=archive_key)
    for one in wanted:
        ref = str(one.get("ref") or "")
        path = file_path(ref)
        alk = by_ref.get(ref)
        if alk is not None:
            results.append(
                {
                    "ref": ref,
                    "resolved": alk.get("resolved") is not False,
                    "matched_digest": bare_digest(alk["matched_digest"]),
                }
            )
            continue
        body = texts.get(path) or texts.get(f"knowledge/{path}")
        if body is None:
            results.append({"ref": ref, "resolved": False, "matched_digest": None})
            continue
        results.append(
            resolve_file_text(
                path,
                body.decode("utf-8", errors="replace"),
                str(one.get("quote") or ""),
                archives.sha256_hex(body),
            )
            | {"ref": ref}
        )
    return results


def _label(
    kind: str,
    ref: str,
    quote: str,
    rules: list[str],
    matched: int | None,
    created: Any = None,
) -> str:
    if kind == "rule":
        text = rules[matched] if matched is not None else quote
        return text[:80]
    if kind == "file":
        return file_path(ref).rsplit("/", 1)[-1]
    if kind == "chat_message":
        return (
            f"Builder chat, {created:%d %b %Y}"
            if created is not None
            else "Builder chat"
        )
    if kind == "evidence_case":
        # Built from a real conversation or record, never a replay of it.
        return f"Based on evidence case {ref.removeprefix('evidence:')}, not an exact replay"
    if kind == "requirement":
        return "Stated by you"
    return "Assumption"


def resolve(
    environment: Any,
    environment_version: Any,
    by_key: Mapping[str, list[Mapping[str, Any]]],
    *,
    stored: Mapping[str, list[Mapping[str, Any]]] | None = None,
) -> dict[str, list[SourceView]]:
    """Every key's sources as views, in at most two queries whatever the suite's size."""
    from simulate.models import HostedHarnessConversationMessage

    rules = _rules(environment_version)
    digests = _file_digests(environment_version)
    chat_ids = {
        str(one.get("ref") or "").removeprefix("chat:")
        for sources in by_key.values()
        for one in sources
        if isinstance(one, Mapping) and one.get("kind") == "chat_message"
    }
    messages: dict[str, Any] = {}
    if chat_ids:
        valid = []
        for one in chat_ids:
            try:
                valid.append(str(uuid.UUID(one)))
            except ValueError:
                continue
        messages = {
            str(row["id"]): row["created_at"]
            for row in HostedHarnessConversationMessage.no_workspace_objects.filter(
                id__in=valid, conversation__job_id=environment.id
            ).values("id", "created_at")
        }
    views: dict[str, list[SourceView]] = {}
    for key, sources in by_key.items():
        kept = {
            str(one.get("ref")): one
            for one in (stored or {}).get(key) or []
            if isinstance(one, Mapping)
        }
        out: list[SourceView] = []
        for one in sources or []:
            if not isinstance(one, Mapping):
                continue
            kind = str(one.get("kind") or "assumption")
            ref = str(one.get("ref") or "")
            quote = str(one.get("quote") or "")
            resolved, problem, matched, created, link = True, "", None, None, None
            if kind == "rule":
                matched = _rule_match(quote, rules)
                resolved = matched is not None
                problem = (
                    ""
                    if resolved
                    else "no rule of this version contains the quoted words"
                )
            elif kind == "file":
                path = file_path(ref)
                result = kept.get(ref) or {}
                current = _digest_for(path, digests)
                if current is None:
                    resolved, problem = (
                        False,
                        "the file is not in this environment version",
                    )
                elif not result:
                    # Never read against a file yet: an unsaved case, or one not validated since.
                    resolved, problem = False, FILE_UNCHECKED
                elif bare_digest(result.get("matched_digest")) != current:
                    resolved, problem = False, FILE_CHANGED
                else:
                    resolved = result.get("resolved") is True
                    problem = (
                        "" if resolved else "the file does not contain the quoted words"
                    )
            elif kind == "chat_message":
                message_id = ref.removeprefix("chat:")
                created = messages.get(message_id)
                resolved = created is not None
                problem = (
                    ""
                    if resolved
                    else "no message with that id in this environment's chat"
                )
            elif kind == "evidence_case":
                resolved, problem = False, "evidence imports not available"
            elif kind == "requirement":
                resolved = bool(quote.strip())
                problem = (
                    "" if resolved else "a requirement needs the person's own words"
                )
            out.append(
                SourceView(
                    kind=kind,
                    ref=ref,
                    quote=quote,
                    supports=str(one.get("supports") or ""),
                    resolved=resolved,
                    label=_label(kind, ref, quote, rules, matched, created),
                    link=link,
                    problem=problem,
                )
            )
        views[key] = out
    return views
