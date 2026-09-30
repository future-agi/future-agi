"""Writes to an environment's authored scenario suite."""

from __future__ import annotations

import logging
import re
from typing import Any

from simulate.models import (
    HostedHarnessJob,
    HostedHarnessScenario,
    HostedHarnessStageOutput,
)
from simulate.services.harness_scenarios import (
    EDITABLE_BEHAVIOUR_FIELDS,
    EDITABLE_PERSONA_FIELDS,
    EDITABLE_TEXT_FIELDS,
)

logger = logging.getLogger(__name__)


_ORDINAL_WORDS = {
    word: number
    for number, word in enumerate(
        (
            "first second third fourth fifth sixth seventh eighth ninth tenth "
            "eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth "
            "eighteenth nineteenth twentieth"
        ).split(),
        start=1,
    )
}


def scenarios_meant(
    said: Any, suite: list[dict], numbering: dict[int, str] | None = None
) -> list[str]:
    """Scenario names from "4", "12-30", "12, 15, 18" or a name; `numbering` maps stored numbers."""
    by_number = numbering or {
        position: str(one.get("name") or "") for position, one in enumerate(suite, 1)
    }
    known = {str(one.get("name") or "") for one in suite}
    keys = {
        str(one.get("scenario_key") or ""): str(one.get("name") or "") for one in suite
    }
    from simulate.utils.scenario_keys import canonical_scenario_key

    # Older suites carry no scenario_key, so a row's hyphenated key must still find its name.
    loose = {
        canonical_scenario_key(label): str(one.get("name") or "")
        for one in suite
        for label in (one.get("name"), one.get("scenario_key"))
        if label
    }
    found: list[str] = []

    def take(name: str) -> None:
        if name and name not in found:
            found.append(name)

    for piece in said if isinstance(said, (list, tuple)) else [said]:
        for part in re.split(r"[,\s]+(?:and\s+)?", str(piece or "").strip()):
            part = part.strip().strip(".")
            if not part:
                continue
            if part in known:
                take(part)
                continue
            if part in keys:
                take(keys[part])
                continue
            if canonical_scenario_key(part) in loose:
                take(loose[canonical_scenario_key(part)])
                continue
            span = re.fullmatch(r"(\d+)\s*(?:-|–|to|through)\s*(\d+)", part)
            if span:
                low, high = sorted((int(span.group(1)), int(span.group(2))))
                for number in range(low, high + 1):
                    take(by_number.get(number, ""))
                continue
            # "#4", "4th", "the fourth".
            plain = re.sub(
                r"^(?:the|scenario|no\.?|#)\s*", "", part, flags=re.IGNORECASE
            )
            plain = re.sub(r"(?<=\d)(?:st|nd|rd|th)$", "", plain, flags=re.IGNORECASE)
            if plain.isdigit():
                take(by_number.get(int(plain), ""))
                continue
            if plain.lower() in _ORDINAL_WORDS:
                take(by_number.get(_ORDINAL_WORDS[plain.lower()], ""))
    return found


def amend_suite(
    job: HostedHarnessJob, changes: list[dict[str, Any]], *, rework: bool = True
) -> tuple[dict[str, Any], int]:
    """Apply changes to a locked job's suite: one receipt per scenario each change names.

    The caller holds the job row lock inside a transaction.
    """
    result = _amend(job, changes, rework)
    return result if isinstance(result, tuple) else (result, 200)


def _amend(job: HostedHarnessJob, changes: list[dict[str, Any]], rework: bool):
    from simulate.services.hosted_harness_gateway import (
        AuthoringArchiveKept,
        push_scenarios_into_live_sandbox,
        rewrite_authoring_scenarios,
        rewrite_conversation_scenarios,
    )

    output = (
        HostedHarnessStageOutput.no_workspace_objects.select_for_update()
        .filter(job=job, kind="scenarios")
        .first()
    )
    suite = list(output.data or []) if output is not None else None
    if suite is None:
        suite = [
            dict(one)
            for one in next(
                (
                    item.get("data") or []
                    for item in (job.stage_outputs or [])
                    if item.get("kind") == "scenarios"
                ),
                [],
            )
        ]
    if not suite:
        return (
            {
                "error": "no_authored_suite",
                "message": "this run has no authored scenarios to amend",
            },
            409,
        )
    by_name = {str(one.get("name") or ""): one for one in suite}
    receipts = []
    touched = False
    changed: set[str] = set()
    # One change may name many scenarios; each gets its own receipt.
    spread = []
    for change in changes:
        said = change.get("scenarios") or change.get("scenario")
        # Resolve numbers against stored numbering, not list position.
        numbering = {
            row.number: row.name or row.scenario_key
            for row in HostedHarnessScenario.no_workspace_objects.filter(
                job=job, number__isnull=False
            )
        }
        meant = scenarios_meant(said, suite, numbering or None)
        if not meant:
            receipts.append(
                {
                    "scenario": str(change.get("scenario") or ""),
                    "outcome": "refused",
                    "why": "nothing in this suite answers to that",
                }
            )
            continue
        spread.extend({**change, "scenario": name} for name in meant)
    for change in spread:
        name = str(change.get("scenario") or "")
        target = by_name.get(name)
        if target is None:
            receipts.append(
                {
                    "scenario": name,
                    "outcome": "refused",
                    "why": "no scenario of that name in this suite",
                }
            )
            continue
        op = str(change.get("op") or "")
        if op == "drop":
            if not rework:
                receipts.append(
                    {
                        "scenario": name,
                        "outcome": "refused",
                        "why": "dropping a scenario changes the suite, so it needs a re-proof",
                    }
                )
                continue
            suite = [one for one in suite if one is not target]
            by_name.pop(name, None)
            touched = True
            receipts.append({"scenario": name, "outcome": "applied", "why": "dropped"})
            continue
        if op == "set_field":
            field = str(change.get("field") or "")
            if field in EDITABLE_TEXT_FIELDS:
                pass
            elif field in EDITABLE_BEHAVIOUR_FIELDS:
                if not rework:
                    receipts.append(
                        {
                            "scenario": name,
                            "outcome": "refused",
                            "why": f"{field} changes what the run does, so it needs a re-proof",
                        }
                    )
                    continue
            else:
                receipts.append(
                    {
                        "scenario": name,
                        "outcome": "refused",
                        "why": f"{field} is not editable: it is proved, not described",
                    }
                )
                continue
            target[field] = change.get("value")
            touched = True
            changed.add(name)
            receipts.append(
                {
                    "scenario": name,
                    "outcome": "applied",
                    "why": f"{field} updated",
                }
            )
            continue
        if op == "set_persona":
            if not rework:
                receipts.append(
                    {
                        "scenario": name,
                        "outcome": "refused",
                        "why": "the persona is what the agent hears, so it needs a re-proof",
                    }
                )
                continue
            given = dict(change.get("persona") or {})
            unknown = sorted(set(given) - EDITABLE_PERSONA_FIELDS)
            if unknown:
                receipts.append(
                    {
                        "scenario": name,
                        "outcome": "refused",
                        "why": f"not editable on a persona: {', '.join(unknown)}",
                    }
                )
                continue
            persona = dict(target.get("persona") or {})
            persona.update(
                {key: value for key, value in given.items() if value is not None}
            )
            target["persona"] = persona
            touched = True
            changed.add(name)
            receipts.append(
                {
                    "scenario": name,
                    "outcome": "applied",
                    "why": "persona updated",
                }
            )
            continue
        receipts.append(
            {
                "scenario": name,
                "outcome": "refused",
                "why": f"unknown change {op!r}",
            }
        )
    # Only the scenarios this amend changed pass the gates again.
    if changed:
        try:
            from fi.alk.harness.scenario import Scenario, scenario_edit_problems
        except (
            ImportError
        ):  # the harness package ships in the runner image, not the web backend
            scenario_edit_problems = None

        rejected = []
        for one in (
            [one for one in suite if str(one.get("name") or "") in changed]
            if scenario_edit_problems
            else ()
        ):
            try:
                problems = scenario_edit_problems(Scenario.model_validate(one))
            except (
                Exception
            ):  # noqa: BLE001 - a document we cannot read is the edit's fault
                problems = ["the edited scenario could not be read"]
            if problems:
                rejected.append((str(one.get("name") or ""), problems))
        if rejected:
            named = {name for name, _ in rejected}
            return {
                "receipts": [
                    {
                        "scenario": name,
                        "outcome": "refused",
                        "why": "; ".join(problems),
                    }
                    for name, problems in rejected
                ]
                + [
                    one
                    for one in receipts
                    if one.get("scenario") not in named
                    and one.get("outcome") == "refused"
                ]
            }
    if touched:
        # The archive a run replays changes first; if it cannot, nothing changes.
        try:
            rewrite_authoring_scenarios(job, suite)
            rewrite_conversation_scenarios(job, suite)
        except AuthoringArchiveKept as kept:
            return {
                "receipts": [
                    (
                        {
                            **one,
                            "outcome": "refused",
                            "why": f"nothing changed: {kept}",
                        }
                        if one.get("outcome") == "applied"
                        else one
                    )
                    for one in receipts
                ]
            }
        if output is not None:
            output.data = suite
            output.summary = f"{len(suite)} pre-authored scenarios"
            output.save(update_fields=["data", "summary", "updated_at"])
        else:
            job.stage_outputs = [
                {**item, "data": suite} if item.get("kind") == "scenarios" else item
                for item in (job.stage_outputs or [])
            ]
            job.save(update_fields=["stage_outputs", "updated_at"])
        # Write to the archive, the live guest and the index, or the edit reverts or hides.
        try:
            from simulate.services.harness_scenarios import index_scenarios

            index_scenarios(job, suite, prune=True)
        except Exception:  # noqa: BLE001 - the edit itself applied; the index can lag
            logger.warning(
                "harness_scenario_reindex_failed job_id=%s",
                job.id,
                exc_info=True,
            )
        delivered = push_scenarios_into_live_sandbox(job, suite)
        if delivered:
            receipts = [
                {**one, "outcome": "queued"} if one.get("outcome") == "applied" else one
                for one in receipts
            ]
    return {"receipts": receipts}


class ScenarioChangeRefused(Exception):
    """A change the suite cannot take, with a stable code for the caller."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _locked_environment(
    environment_id, expected_revision: str | None
) -> HostedHarnessJob:
    job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
        id=environment_id
    )
    if job.state != HostedHarnessJob.State.COMPLETED:
        raise ScenarioChangeRefused(
            "environment_not_ready", "scenarios can be changed once the build finishes"
        )
    current = _revision(job)
    if expected_revision and current and expected_revision != current:
        raise ScenarioChangeRefused(
            "scenario_suite_changed",
            "the scenarios changed since they were loaded; reload and try again",
        )
    return job


def _revision(job: HostedHarnessJob) -> str:
    """The suite's current revision, derived from its archive for older environments."""
    from simulate.services.hosted_harness_conversation import (
        environment_authoring_revision,
    )

    return environment_authoring_revision(job)


def _check_values(
    contract: dict[str, Any], fields: dict[str, Any], persona: dict[str, Any]
) -> None:
    allowed = set(contract["editable_fields"]) - EDITABLE_TEXT_FIELDS
    refused = sorted(set(fields) - allowed) + sorted(
        set(persona) - set(contract["persona_fields"])
    )
    if refused:
        raise ScenarioChangeRefused(
            "field_not_editable",
            f"not editable without a re-proof: {', '.join(refused)}",
            400,
        )
    noise = fields.get("background_noise")
    if noise is not None and noise not in contract["noise_choices"]:
        raise ScenarioChangeRefused(
            "value_not_allowed", f"unknown background noise: {noise}", 400
        )
    for field, value in persona.items():
        choices = contract["persona_choices"].get(field) or []
        given = value if isinstance(value, list) else [value]
        if field == "languages" and not isinstance(value, list):
            raise ScenarioChangeRefused("value_not_allowed", "languages is a list", 400)
        wrong = [one for one in given if one not in choices]
        if wrong or not given:
            raise ScenarioChangeRefused(
                "value_not_allowed",
                f"not a {field} the editor offers: {wrong or value}",
                400,
            )


def _applied(body: dict[str, Any], status: int) -> list[dict[str, Any]]:
    if status != 200:
        raise ScenarioChangeRefused(
            body.get("error", "scenario_change_refused"),
            body.get("message", ""),
            status,
        )
    receipts = body.get("receipts") or []
    if receipts and all(one.get("outcome") == "refused" for one in receipts):
        raise ScenarioChangeRefused(
            "scenario_change_refused",
            "; ".join(str(one.get("why") or "") for one in receipts),
        )
    return receipts


def edit_scenario(
    environment: HostedHarnessJob,
    scenario_id,
    *,
    fields: dict[str, Any],
    persona: dict[str, Any],
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Replace a scenario's directly editable fields; nothing is re-proved."""
    from django.db import transaction

    from simulate.services.harness_scenarios import (
        editing_contract,
        is_spoken_suite,
        scenario_row,
    )

    if not fields and not persona:
        raise ScenarioChangeRefused("nothing_to_change", "no field was given", 400)
    with transaction.atomic():
        job = _locked_environment(environment.id, expected_revision)
        row = HostedHarnessScenario.no_workspace_objects.filter(
            job=job, id=scenario_id
        ).first()
        if row is None:
            raise ScenarioChangeRefused("scenario_not_found", "scenario not found", 404)
        _check_values(editing_contract(is_spoken_suite(job)), fields, persona)
        changes = [
            {
                "op": "set_field",
                "scenario": row.scenario_key,
                "field": field,
                "value": value,
            }
            for field, value in fields.items()
        ]
        if persona:
            changes.append(
                {"op": "set_persona", "scenario": row.scenario_key, "persona": persona}
            )
        receipts = _applied(*amend_suite(job, changes, rework=True))
        revision = _revision(job)
    row = (
        HostedHarnessScenario.no_workspace_objects.filter(id=row.id)
        .select_related("scenario", "call_execution")
        .first()
    )
    return {
        "receipts": receipts,
        "revision": revision,
        "scenario": scenario_row(row) if row is not None else None,
    }


def delete_scenarios(
    environment: HostedHarnessJob,
    scenario_ids: list,
    *,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Remove scenarios from the suite; their rows stay for the runs that used them."""
    from django.db import transaction

    with transaction.atomic():
        job = _locked_environment(environment.id, expected_revision)
        keys = list(
            HostedHarnessScenario.no_workspace_objects.filter(
                job=job, id__in=scenario_ids
            ).values_list("scenario_key", flat=True)
        )
        if len(keys) != len(set(scenario_ids)):
            raise ScenarioChangeRefused("scenario_not_found", "scenario not found", 404)
        receipts = _applied(
            *amend_suite(job, [{"op": "drop", "scenarios": keys}], rework=True)
        )
        return {"receipts": receipts, "revision": _revision(job), "scenario": None}


def request_scenario_change(
    environment: HostedHarnessJob,
    *,
    kind: str,
    instruction: str,
    scenario_ids: list,
    count: int | None,
    client_request_id: str,
    base_url: str,
):
    """Hand a change that needs re-proving to the environment's builder agent.

    The agent rewrites and re-proves the scenarios in its chat workspace; when the turn
    finishes, the chat publish makes the result the environment's new snapshot.
    """
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_conversation import (
        check_builder_workspace,
        send_builder_message,
    )

    if environment.state != HostedHarnessJob.State.COMPLETED:
        raise ScenarioChangeRefused(
            "environment_not_ready", "scenarios can be changed once the build finishes"
        )
    instruction = (instruction or "").strip()
    if kind == "revise":
        found = HostedHarnessScenario.no_workspace_objects.filter(
            job=environment, id__in=scenario_ids
        ).count()
        if found != len(set(scenario_ids)):
            raise ScenarioChangeRefused("scenario_not_found", "scenario not found", 404)
        content = (
            "Revise the selected scenarios as follows, re-prove each one, "
            f"and save the suite: {instruction}"
        )
    else:
        guidance = f" {instruction}" if instruction else ""
        content = (
            f"Add exactly {count} new scenario{'s' if count != 1 else ''} to this suite."
            f"{guidance} Keep every existing scenario as it is, prove each new one, "
            "then save the suite."
        )
    try:
        check_builder_workspace(environment)
        return send_builder_message(
            environment,
            content=content,
            client_request_id=client_request_id,
            base_url=base_url,
            payload={
                "change_kind": kind,
                "scenario_ids": [str(one) for one in scenario_ids],
            },
        )
    except HostedHarnessError as exc:
        raise ScenarioChangeRefused(exc.code, exc.message, exc.status_code) from exc
