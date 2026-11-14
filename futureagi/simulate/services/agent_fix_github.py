"""Ship a GitHub agent's winner, or a candidate only its must-pass check refused, as a pull
request on its repository: the candidate's patch, applied to the commit the fix started
from, on a branch of its own."""

import re
import tempfile
from math import floor
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import structlog
from django.conf import settings

from simulate.models import AgentFixCandidate, HarnessAgentVersionRuntime
from simulate.pydantic_schemas.agent_fix import HELD_BACK_VERIFIED_MIN, EvalGrading
from simulate.selectors.agent_fix import (
    eval_review,
    held_back_cases,
    limited_validation,
    overridable_regressions,
    verdict_tiers,
)
from simulate.services.agent_fix_engine import load_object
from simulate.services.agent_fix_runs import (
    base_moved,
    record_override,
    require_shippable,
)
from simulate.services.harness_github import safe_repository, short_ref
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_gateway import (
    GitHubAppTokenProvider,
    github_checkout,
    run_git,
)

logger = structlog.get_logger(__name__)

_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
_AUTHOR = ("Future AGI", "fix-my-agent@futureagi.com")
_NO_DRAFTS = "draft pull requests are not supported"
_TITLE_CHARS = 72
# GitHub refuses a pull request body over 65,536 characters; this leaves it room.
_BODY_CHARS = 60_000
# The changed files a body lists; the rest are counted.
_FILES_LISTED = 50


def open_pull_request(
    candidate: AgentFixCandidate, *, user: Any, override_reason: str = ""
) -> str:
    """Open the candidate's pull request once and return its URL. Refused when the ref the
    agent runs from (its branch or tag) moved since the fix's commit, whatever the reason
    given: the fix proved its change on that commit alone."""
    require_shippable(candidate, override_reason=override_reason)
    if candidate.pull_request_url:
        return candidate.pull_request_url
    fix_run = candidate.fix_run
    runtime = HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version_id=fix_run.base_agent_version_id,
        source_kind=HarnessAgentVersionRuntime.SourceKind.GITHUB,
    ).first()
    installation_id = ""
    if runtime is not None:
        access = (runtime.agent_payload or {}).get("source_access") or {}
        installation_id = str(access.get("installation_id") or "")
    if runtime is None or not installation_id:
        raise HostedHarnessError(
            "fix_github_not_connected",
            "A pull request needs the agent's repository connected through the Future AGI"
            " GitHub App; download the patch instead",
            status_code=422,
        )
    source = runtime.source or {}
    repository = str(source.get("repository") or "")
    if not safe_repository(repository):
        raise HostedHarnessError(
            "github_repository_invalid", "invalid GitHub repository", status_code=422
        )
    run = fix_run.source_run
    run_source = ((run.payload if run else None) or {}).get("source") or {}
    commit = str(run_source.get("commit_sha") or "")
    if not commit:
        # The patch was made on the commit the run checked out; a branch tip may differ.
        raise HostedHarnessError(
            "fix_github_commit_unknown",
            "the commit the run checked out is not known; download the patch instead",
            status_code=409,
        )
    patch = load_object(candidate.patch_object_key) or b""
    branch = f"futureagi/fix-{str(fix_run.id)[:8]}-{candidate.attempt}"
    ref = str(source.get("ref") or "")
    with GitHubAppTokenProvider.from_settings().credential(installation_id) as token:
        base = _base_branch(repository, ref, token)
        head = _ref_head(repository, ref, base, token)
        if head is not None and head != commit:
            name = short_ref(ref) if ref.startswith("refs/tags/") else base
            raise base_moved(f"{name} @ {commit[:7]}", f"{name} @ {head[:7]}")
        # Before the push: the commit carries the reason, so a retry pushes the same commit.
        record_override(candidate, reason=override_reason, user=user)
        title, body = _description(candidate, patch)
        _push(repository, commit, branch, patch, candidate, title, body, token)
        url = _pull_request(
            repository,
            branch,
            base,
            title,
            body,
            token,
            draft=limited_validation(fix_run) or bool(candidate.override_reason),
        )
    AgentFixCandidate.no_workspace_objects.filter(id=candidate.id).update(
        pull_request_url=url
    )
    candidate.pull_request_url = url
    logger.info(
        "agent_fix_pull_request_opened",
        fix_run=str(fix_run.id),
        candidate=str(candidate.id),
    )
    return url


def _percent(rate: float) -> str:
    # Half up, as the fix page rounds: 62.5% reads 63% in both places.
    return f"{floor(rate * 100 + 0.5)}%"


def _evidence(verdict: dict) -> str:
    """The pass rates a reviewer weighs and the must-pass cases it broke, as the fix page's
    headline says them."""
    rates = [
        f"{name} pass rate {_percent(before)} → {_percent(after)}"
        for name, (before, after) in (
            ("held-back", verdict.get("held_back_pass_rate") or (None, None)),
            ("target", verdict.get("target_pass_rate") or (None, None)),
        )
        if after is not None
    ]
    if not rates:
        return ""
    broke = len(verdict.get("must_pass_regressions") or [])
    return ", ".join([*rates, f"broke {broke or 'none'}"])


# The FE says the same where the user ships.
_EVALS_INCOMPLETE = (
    "**Platform evals did not finish on this candidate,** so their results are missing."
    " Review it closely before you merge."
)


def _evals_note(candidate: AgentFixCandidate) -> str:
    """The platform evals the winner does worse on than the current version. They do not
    decide a fix, so the reviewer reads them here, or that none did, or that they did not
    finish. Only a winner is graded."""
    if candidate.outcome != AgentFixCandidate.Outcome.WINNER:
        return ""
    review = eval_review(candidate.fix_run)
    lines = [_EVALS_INCOMPLETE] if review.grading == EvalGrading.INCOMPLETE else []
    if review.warnings:
        lines.append(
            "**Platform evals this change does worse on.** They do not pick the winner;"
            " review them before you merge."
        )
        lines += [
            f"- {one.name}: {_percent(one.pass_rate)} passed ({one.trials} trials),"
            f" {_percent(one.base_pass_rate)} on the current version"
            f" ({one.base_trials} trials)"
            for one in review.warnings
        ]
    elif review.grading == EvalGrading.DONE and review.compared:
        lines.append("Platform evals: none did worse than the current version.")
    return "\n".join(lines)


def _must_pass_check(check: str) -> str:
    """A must-pass check, sent as "scenario key: sub-goal", as its case and its check."""
    case, _, sub_goal = check.partition(": ")
    named = f"Must-pass case “{case}”"
    return f"{named}: its “{sub_goal}” check" if sub_goal else named


def _limited_note(candidate: AgentFixCandidate) -> str:
    held = held_back_cases(candidate.fix_run)
    which = (
        "No failing cases were"
        if not held
        else f"Only {held} failing case was"
        if held == 1
        else f"Only {held} failing cases were"
    )
    return (
        f"**Limited validation.** {which} held back from the epoch to verify it; it takes"
        f" {HELD_BACK_VERIFIED_MIN} or more. Check the changed behavior yourself before"
        " you merge."
    )


def _override_note(candidate: AgentFixCandidate) -> str:
    """What a candidate the must-pass check refused is opened with, and the user's reason."""
    checks = "\n".join(
        f"- {_must_pass_check(check)}" for check in overridable_regressions(candidate)
    )
    reason = "\n".join(f"> {line}" for line in candidate.override_reason.splitlines())
    return (
        "**Known regression.** Epoch did not accept this change: it broke these"
        f" must-pass checks.\n\n{checks}\n\nIt was opened anyway, for this reason:"
        f"\n\n{reason}"
    )


def _changed_files(patch: bytes) -> list[tuple[str, int, int]]:
    """Each file the patch changes, in patch order, with its added and removed lines."""
    files: dict[str, list[int]] = {}
    path, in_hunk = None, False
    for line in patch.decode("utf-8", "replace").splitlines():
        if line.startswith("diff --git "):
            path, in_hunk = line.rsplit(" b/", 1)[-1], False
            files.setdefault(path, [0, 0])
        elif path is None:
            continue
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            files[path][0] += 1
        elif in_hunk and line.startswith("-"):
            files[path][1] += 1
    return [(name, added, removed) for name, (added, removed) in files.items()]


def _causes(candidate: AgentFixCandidate) -> list[str]:
    """What the fix's diagnosis found its target cases fail on, in its own words."""
    causes = (candidate.fix_run.diagnosis or {}).get("causes") or []
    return [str(cause).strip() for cause in causes if str(cause).strip()]


def _title(
    candidate: AgentFixCandidate, files: list[tuple[str, int, int]], marks: str
) -> str:
    """What the pull request is for and where: the causes the diagnosis found, fixed in
    the files the whole diff changes. Made only of counts and a file name, so it never
    cuts a sentence; the first wording that fits is used."""
    name = f"Epoch ({marks})" if marks else "Epoch"
    causes = len(_causes(candidate))
    counted = _plural(len(files), "file", "files") if files else "the agent"
    places = [Path(files[0][0]).name, counted] if len(files) == 1 else [counted]
    if not causes:
        wordings = [f"{name}: change {place}" for place in places]
    else:
        fixes = _plural(causes, "diagnosed cause", "diagnosed causes")
        wordings = [f"{name}: fix {fixes} in {place}" for place in places]
        # Both marks leave room only for the shorter word.
        wordings.append(
            f"{name}: fix {_plural(causes, 'cause', 'causes')} in {counted}"
        )
    return next((one for one in wordings if len(one) <= _TITLE_CHARS), wordings[-1])


def _cell(text: Any) -> str:
    """A value as one table cell: on one line, with its pipes escaped."""
    return " ".join(str(text).split()).replace("|", "\\|")


# The fix page's words for a check and for the parts of the reply time and cost check
# (fixRunView.js: CHECK_LABELS, checkLabel, describePart, unknownWhy). The engine's ids
# are never shown.
_CHECK_LABELS = {
    "static": "Change is valid",
    "target": "Target scenarios",
    "held-back": "Held-back scenarios",
    "must-pass": "Must-pass scenarios",
    "repeats": "Holds over repeats",
    "ceilings": "Reply time and cost",
}
_PART_LABELS = {"reply latency": "Reply time", "agent cost": "Agent's cost"}
_UNKNOWN_PARTS = {"reply latency": "Reply time unknown", "agent cost": "Cost unknown"}
_UNTRACED_COST = (
    "This agent's model calls weren't measured. Epoch measures calls made through the"
    " OpenAI or Anthropic Python SDK on its own. For other agents, add traceAI tracing."
)


def _check_label(name: str, request: dict) -> str:
    if name == "model-lock":
        many = len(request.get("allowed_models") or []) > 1
        return "Approved models" if many else "Model unchanged"
    if name in _CHECK_LABELS:
        return _CHECK_LABELS[name]
    words = re.sub(r"[-_]+", " ", name)
    return words[:1].upper() + words[1:]


def _result(status: str) -> str:
    # A shipped candidate failed no check but must-pass, and ran every check after it,
    # so a skipped check here ran and could not measure.
    return "Couldn't measure" if status == "skipped" else status.capitalize()


def _part_line(part: dict, request: dict) -> str:
    """A part of the reply time and cost check in words: within its limit, or unknown with
    why and what would make it known (fixRunView.js partDetail, unknownWhy)."""
    status, text = part.get("status"), str(part.get("detail") or "")
    if status == "passed":
        return "Within the limit."
    if status == "failed":
        return "Over the limit."
    if part.get("name") == "reply latency":
        return (
            "The conversations recorded no reply times. Run again; if it repeats, check"
            " that the agent replies within the turn."
        )
    if not request.get("meters_agent_cost"):
        return _UNTRACED_COST
    if "sandbox could not measure" in text:
        return (
            f"{_conversations(text, 'have a model call the sandbox')} made a model call"
            " that couldn't be measured, such as a call through another SDK or a direct"
            " HTTP request. Route that call through the OpenAI or Anthropic SDK, or add"
            " traceAI tracing."
        )
    if re.search("baseline not measured|not verified against the baseline", text):
        return (
            "The current version's cost wasn't measured, so there is nothing to compare"
            " it with. Run the environment again to measure it."
        )
    if "with no cost" in text:
        return (
            "A model the agent called has no price. Add a price for it to measure cost."
        )
    return (
        f"{_conversations(text, 'exported no model call')} returned no model-call"
        " record. Run again to measure them."
    )


def _conversations(text: str, cause: str) -> str:
    """The count in the engine's "3 of 12 finalist conversations …", which the line keeps."""
    found = re.search(rf"(\d+) of (\d+) finalist conversations {cause}", text)
    return f"{found[1]} of {found[2]} conversations" if found else "Some conversations"


def _part_row(part: dict, request: dict) -> str:
    name, status = str(part.get("name", "")), str(part.get("status", ""))
    result = _result(status)
    if status == "skipped":
        result = _UNKNOWN_PARTS.get(name, result)
    label = _cell(_PART_LABELS.get(name, name))
    return f"| ↳ {label} | {result} | {_cell(_part_line(part, request))} |"


def _rate_line(rate: Any, gain: bool, tail: str = "") -> str:
    said = "a clear gain" if gain else "not a clear gain"
    if rate:
        return f"{_percent(rate[0])} → {_percent(rate[1])}{tail}, {said}."
    return f"{said[:1].upper()}{said[1:]}{tail}."


# What a check that passed shows when the verdict holds no number for it.
_PASSED = {
    "static": "Applies cleanly and edits only allowed files.",
    "repeats": "Held up over the repeat runs.",
    "ceilings": "Within the reply-time and cost limits.",
}


def _tier_line(tier: dict, verdict: dict, plan: dict, request: dict) -> str:
    """A check's result in plain words from the verdict's own numbers, as the fix page says
    it (fixRunView.js tierLine). A shipped candidate passed every check but must-pass, or
    could not measure one; the parts of one that could not measure say why."""
    name, passed = tier.get("name"), tier.get("status") == "passed"
    if name == "target":
        return _rate_line(verdict.get("target_pass_rate"), passed)
    if name == "held-back":
        tail = " on scenarios the epoch never saw"
        return _rate_line(verdict.get("held_back_pass_rate"), passed, tail)
    if name == "must-pass":
        broke = len(verdict.get("must_pass_regressions") or [])
        if not passed:
            scenarios = _plural(broke, "must-pass scenario", "must-pass scenarios")
            return f"{scenarios} broke." if broke else "A must-pass scenario broke."
        total, protected = plan.get("must_pass"), plan.get("protected")
        kept = f", including the {protected} you protected" if protected else ""
        if not total:
            return "No must-pass scenario broke."
        return f"None of the {total} must-pass scenarios broke{kept}."
    if name == "model-lock":
        models = request.get("allowed_models") or []
        if passed:
            return (
                f"Called only {', '.join(models)}."
                if models
                else "Called only allowed models."
            )
        sent = (
            "Some conversations sent no traces."
            if request.get("observes_models")
            else "This agent sends no traces."
        )
        return f"{sent} The change names no new model."
    if not passed:
        return ""
    return _PASSED.get(str(name), "Passed.")


# What measuring an agent's model calls on its own reads (fixRunView.js MEASURED_NOTICE).
_MEASURED = (
    "Measured automatically: model, tokens and timing only. Prompts and replies are"
    " never recorded."
)


def _decided_section(tiers: list[dict], verdict: dict, request: dict) -> str:
    """The engine's own account of each check, its statistics included, folded away for a
    reviewer who wants to see how the verdict was reached."""
    lines = []
    for tier in tiers:
        if tier.get("detail"):
            label = _check_label(str(tier.get("name", "")), request)
            lines.append(f"- {label}: {' '.join(str(tier['detail']).split())}")
        lines += [
            f"  - {_PART_LABELS.get(part.get('name'), part.get('name'))}:"
            f" {' '.join(str(part['detail']).split())}"
            for part in tier.get("parts") or []
            if part.get("detail")
        ]
    if not lines:
        return ""
    # The engine reports the held-back pass rate from the repeat runs when they ran.
    pooled = verdict.get("held_back_pass_rate") and any(
        tier.get("name") == "repeats" for tier in tiers
    )
    note = (
        "The held-back pass rate above comes from the repeat runs.\n\n"
        if pooled
        else ""
    )
    listed = "\n".join(lines)
    return f"<details><summary>How it was decided</summary>\n\n{note}{listed}\n\n</details>"


def _verdict_section(verdict: dict, standing: str, request: dict, plan: dict) -> str:
    """The verdict's checks in the fix page's words, each part of one on its own row, and
    the engine's statistics folded below them."""
    tiers = verdict_tiers(verdict, request)
    if not tiers:
        return f"### Verdict: {standing}"
    rows = []
    for tier in tiers:
        rows.append(
            f"| {_cell(_check_label(str(tier.get('name', '')), request))}"
            f" | {_result(str(tier.get('status', '')))}"
            f" | {_cell(_tier_line(tier, verdict, plan, request))} |"
        )
        rows += [_part_row(part, request) for part in tier.get("parts") or []]
    listed = "\n".join(rows)
    table = f"| Check | Result | Detail |\n|---|---|---|\n{listed}"
    # Only when the platform measured the agent's model calls itself, as the fix page.
    measured = (
        f"\n\n{_MEASURED}" if request.get("measured_automatically") is True else ""
    )
    decided = _decided_section(tiers, verdict, request)
    return "\n\n".join(
        part
        for part in (f"### Verdict: {standing}\n\n{table}{measured}", decided)
        if part
    )


def _files_section(files: list[tuple[str, int, int]]) -> str:
    if not files:
        return ""
    rows = "\n".join(
        f"| `{_cell(path)}` | +{added} | −{removed} |"
        for path, added, removed in files[:_FILES_LISTED]
    )
    more = len(files) - _FILES_LISTED
    tail = f"\n\nand {_plural(more, 'more file', 'more files')}" if more > 0 else ""
    return (
        f"### What changed\n\n| File | Added | Removed |\n|---|---|---|\n{rows}{tail}"
    )


def _fit(head: str, tail: str) -> str:
    """The body under GitHub's size limit. Too long, the head is cut at a line and says
    so; the link to the epoch and the closing line always stay."""
    body = f"{head}\n\n{tail}" if head else tail
    if len(body) <= _BODY_CHARS:
        return body
    note = "\n\n*Cut to fit GitHub's size limit. The epoch's page has all of it.*"
    cut = head[: _BODY_CHARS - len(tail) - len(note) - len("\n</details>\n\n")]
    cut = cut[: cut.rfind("\n")] if "\n" in cut else cut
    if cut.count("<details>") > cut.count("</details>"):
        cut += "\n</details>"
    return f"{cut}{note}\n\n{tail}"


def _why_section(candidate: AgentFixCandidate) -> str:
    causes = _causes(candidate)
    if not causes:
        return ""
    listed = "\n".join(f"- {' '.join(cause.split())}" for cause in causes)
    return (
        f"### Why\n\nThe diagnosis found these causes of the failing cases:\n\n{listed}"
    )


def _risks_section(candidate: AgentFixCandidate, verdict: dict) -> str:
    """What the checks could not hold this change to, or flagged without refusing it."""
    # The fix page's words for both (FixVerdict.jsx MustPassBreaks, flakyProtectedText).
    lines = [
        f"- {_must_pass_check(check)} passed less often, within normal noise, so it was"
        " not counted."
        for check in verdict.get("must_pass_within_noise") or []
    ]
    # Each protected case once, by its name, or by its key when the engine sent none.
    flaky: dict[str, list[dict]] = {}
    for row in (candidate.fix_run.report or {}).get("flaky_protected") or []:
        flaky.setdefault(str(row.get("key")), []).append(row)
    for key, rows in flaky.items():
        name = next((row["name"] for row in rows if row.get("name")), key)
        checks = ", ".join(
            f"its “{row.get('sub_goal')}” check passed {row.get('passed')} of"
            f" {row.get('runs')} runs"
            for row in rows
        )
        lines.append(
            f"- Protected case “{name}” is flaky on the current version, so this epoch"
            f" wasn't held to it: {checks}."
        )
    evals = _evals_note(candidate)
    if not lines and not evals:
        return "### What could still go wrong\n\nNothing the checks flagged."
    return "\n\n".join(
        part
        for part in ("### What could still go wrong", "\n".join(lines), evals)
        if part
    )


# The evidence-import page's words for each kind of source: one, many, and where from.
_EVIDENCE_SOURCES = {
    "session": ("session", "sessions", " from Observe"),
    "call": ("call", "calls", " from Observe"),
    "transcript": ("transcript", "transcripts", ""),
    "dataset": ("dataset row", "dataset rows", ""),
}


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _evidence_origin(candidate: AgentFixCandidate) -> str:
    """The evidence the fix started from: how many sources of each import's kind, each
    one added as one case. Never an id, a source's name or its content."""
    imports = (candidate.fix_run.request or {}).get("evidence") or []
    if not imports:
        return ""
    lines = []
    for one in imports:
        cases = len(one.get("scenario_keys") or [])
        kind = _EVIDENCE_SOURCES.get(str(one.get("source_kind")))
        lines.append(
            f"- {_plural(cases, kind[0], kind[1])}{kind[2]}"
            if kind
            else f"- {_plural(cases, 'imported case', 'imported cases')}"
        )
    listed = "\n".join(lines)
    return f"### Evidence\n\nThis epoch started from imported evidence:\n\n{listed}"


def _notes_section(candidate: AgentFixCandidate) -> str:
    """The engine's own summary, labelled: it describes only the candidate's last change."""
    summary = candidate.summary.strip()
    if not summary:
        return ""
    return (
        "<details><summary>Notes from Epoch on the last change of Candidate"
        f" {candidate.attempt}</summary>\n\n{summary}\n\n</details>"
    )


def _fix_link(candidate: AgentFixCandidate) -> str:
    base = str(getattr(settings, "FRONTEND_BASE_URL", "") or "").rstrip("/")
    if not base:
        return ""
    run = candidate.fix_run
    # frontend/src/routes/sections/dashboard.jsx: simulate → environments/:envId →
    # improvements/:fixId, the fix run's page.
    return (
        f"[Open this epoch on Future AGI]({base}/dashboard/simulate/environments/"
        f"{run.environment_id}/improvements/{run.id})"
    )


def _description(candidate: AgentFixCandidate, patch: bytes) -> tuple[str, str]:
    """The title and body: what the whole change does, the verdict it earned, the files it
    changes and why, what could still go wrong, and where it came from. Both say so when
    too few cases were held back to verify it, and when it ships with a known regression
    on the user's reason."""
    verdict = candidate.verdict or {}
    # Not the engine's own reason: the checks below say the same in the page's words.
    rates = _evidence(verdict)
    limited = limited_validation(candidate.fix_run)
    overridden = bool(candidate.override_reason)
    proof = "Checked" if limited or overridden else "Verified"
    standing = (
        "Known regression"
        if overridden
        else "Limited validation"
        if limited
        else "Verified"
    )
    files = _changed_files(patch)
    head = "\n\n".join(
        part
        for part in (
            _override_note(candidate) if overridden else "",
            _limited_note(candidate) if limited else "",
            f"{proof} by Epoch: {rates}" if rates else "",
            _verdict_section(
                verdict,
                standing,
                candidate.fix_run.request or {},
                candidate.fix_run.plan or {},
            ),
            _files_section(files),
            _why_section(candidate),
            _risks_section(candidate, verdict),
            _evidence_origin(candidate),
            _notes_section(candidate),
        )
        if part
    )
    tail = "\n\n".join(
        part
        for part in (_fix_link(candidate), "Opened by Epoch on Future AGI.")
        if part
    )
    body = _fit(head, tail)
    marks = ", ".join(
        mark
        for mark, applies in (
            ("limited validation", limited),
            ("known regression", overridden),
        )
        if applies
    )
    return _title(candidate, files, marks), body


def _push(
    repository: str,
    commit: str,
    branch: str,
    patch: bytes,
    candidate: AgentFixCandidate,
    title: str,
    body: str,
    token: str,
) -> None:
    """Commit the patch on ``commit`` and push it to ``branch``. The commit is the same on
    every call, so a push the branch already has succeeds without changing it, and a branch
    someone changed since is refused, never overwritten."""
    with tempfile.TemporaryDirectory(prefix=f"agent-fix-{candidate.id}-") as temp:
        checkout = Path(temp) / "source"
        environment = github_checkout(repository, commit, checkout, token)
        patch_file = Path(temp) / "candidate.patch"
        patch_file.write_bytes(patch)
        stamp = f"{int(candidate.created_at.timestamp())} +0000"
        environment.update(
            {
                "GIT_AUTHOR_NAME": _AUTHOR[0],
                "GIT_AUTHOR_EMAIL": _AUTHOR[1],
                "GIT_AUTHOR_DATE": stamp,
                "GIT_COMMITTER_NAME": _AUTHOR[0],
                "GIT_COMMITTER_EMAIL": _AUTHOR[1],
                "GIT_COMMITTER_DATE": stamp,
            }
        )
        git = ["git", "-C", str(checkout)]
        for command, code, status_code in (
            ([*git, "apply", "--index", "--binary", str(patch_file)], "patch", 409),
            ([*git, "commit", "--no-verify", "-m", title, "-m", body], "patch", 409),
            ([*git, "push", "origin", f"HEAD:refs/heads/{branch}"], "push", 502),
        ):
            returncode, detail = run_git(command, environment, token)
            if returncode:
                raise _git_error(repository, branch, code, status_code, detail)


def _git_error(
    repository: str, branch: str, step: str, status_code: int, detail: str
) -> HostedHarnessError:
    lowered = detail.lower()
    if step == "push" and ("403" in lowered or "permission to" in lowered):
        return _no_write_access(repository)
    if step == "push" and ("rejected" in lowered or "non-fast-forward" in lowered):
        return HostedHarnessError(
            "fix_github_branch_changed",
            f"the branch {branch} changed on GitHub since the pull request was made",
            status_code=409,
        )
    return HostedHarnessError(
        f"fix_github_{step}_failed",
        detail.strip()[:500],
        status_code=status_code,
        retryable=status_code >= 500,
    )


def _no_write_access(repository: str) -> HostedHarnessError:
    return HostedHarnessError(
        "fix_github_no_write_access",
        f"the Future AGI GitHub App cannot write to {repository}: approve its request for"
        " contents and pull requests write access",
        status_code=422,
    )


def _base_branch(repository: str, ref: str, token: str) -> str:
    """The branch the agent is connected to; the repository's default when it names a tag
    or a commit, or nothing."""
    name = short_ref(ref)
    if name and not ref.startswith("refs/tags/") and not _COMMIT.fullmatch(name):
        return name
    return _api("GET", f"/repos/{repository}", token).json()["default_branch"]


def _ref_head(repository: str, ref: str, branch: str, token: str) -> str | None:
    """The commit the agent's ref points at on GitHub now: a tag's commit, else the head
    of ``branch``, the branch it runs from. None for a ref pinned to a commit, which never
    moves."""
    name = short_ref(ref)
    if name and _COMMIT.fullmatch(name):
        return None
    if ref.startswith("refs/tags/"):
        return _api("GET", f"/repos/{repository}/commits/{quote(name)}", token).json()[
            "sha"
        ]
    return _api(
        "GET", f"/repos/{repository}/git/ref/heads/{quote(branch, safe='/')}", token
    ).json()["object"]["sha"]


def _pull_request(
    repository: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    token: str,
    *,
    draft: bool,
) -> str:
    """Open the pull request, or find the one already open for ``branch``. A repository
    without draft pull requests gets a normal one, and its title still says what it is."""
    path = f"/repos/{repository}/pulls"
    pull = {"title": title, "head": branch, "base": base, "body": body, "draft": draft}
    response = _api("POST", path, token, json=pull)
    if response.status_code == 422 and _NO_DRAFTS in response.text.lower():
        response = _api("POST", path, token, json={**pull, "draft": False})
    if response.status_code == 201:
        return response.json()["html_url"]
    if response.status_code == 403:
        raise _no_write_access(repository)
    if response.status_code == 422 and "already exists" in response.text:
        owner = repository.split("/")[0]
        found = _api(
            "GET",
            f"/repos/{repository}/pulls",
            token,
            params={"head": f"{owner}:{branch}", "state": "all"},
        ).json()
        if found:
            return found[0]["html_url"]
    raise HostedHarnessError(
        "fix_github_pull_request_failed",
        f"GitHub refused the pull request: {response.text[:300]}",
        status_code=502,
        retryable=response.status_code >= 500,
    )


def _api(method: str, path: str, token: str, **kwargs) -> requests.Response:
    try:
        response = requests.request(
            method,
            settings.GITHUB_API_ENDPOINT.rstrip("/") + path,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=(5, 30),
            **kwargs,
        )
    except requests.RequestException as exc:
        raise HostedHarnessError(
            "fix_github_unreachable",
            "GitHub could not be reached",
            status_code=502,
            retryable=True,
        ) from exc
    if method == "GET" and response.status_code != 200:
        raise HostedHarnessError(
            "fix_github_pull_request_failed",
            f"GitHub refused {path}: {response.status_code}",
            status_code=502,
            retryable=response.status_code >= 500,
        )
    return response
