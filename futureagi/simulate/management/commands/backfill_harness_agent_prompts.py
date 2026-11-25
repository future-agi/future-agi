"""
Backfill the full agent prompt onto hosted harness agents that stored only the contract excerpt.

Usage:
    python manage.py backfill_harness_agent_prompts --dry-run
    python manage.py backfill_harness_agent_prompts
    python manage.py backfill_harness_agent_prompts --job <job_id> [--job <job_id> ...]
"""

from __future__ import annotations

import re
from collections import defaultdict
from contextlib import nullcontext
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from simulate.models import AgentVersion, CallExecution, HostedHarnessJob

PROMPT_METADATA_KEYS = ("agent_description", "agent_prompt")


def _normalised(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _full_prompt(job: HostedHarnessJob) -> str:
    agent = (job.payload or {}).get("agent") or {}
    if str(agent.get("connector") or "") != "phone":
        return ""
    return str((agent.get("config") or {}).get("target_system_prompt") or "").strip()


def _excerpt(job: HostedHarnessJob) -> str:
    for output in job.stage_outputs or []:
        if isinstance(output, dict) and output.get("kind") == "contract":
            data = output.get("data")
            if isinstance(data, dict):
                return str(data.get("system_prompt_excerpt") or "").strip()
    return ""


def _is_shortened(stored: str, full: str, excerpts: set[str]) -> bool:
    """True when ``stored`` is a shorter piece of ``full``: a prefix of it or a contract excerpt."""
    value = _normalised(stored)
    if not value or value == _normalised(full):
        return False
    return _normalised(full).startswith(value) or value in excerpts


class Command(BaseCommand):
    help = (
        "Replace the contract excerpt stored as a hosted harness agent's prompt with the full "
        "phone-connector prompt, on the agent definition, its versions and its calls, so evals "
        "re-run on existing calls see the complete instructions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the changes without writing them.",
        )
        parser.add_argument(
            "--job",
            action="append",
            default=[],
            help="Limit to these hosted harness job ids (repeatable).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        jobs = HostedHarnessJob.no_workspace_objects.select_related(
            "run_test__agent_definition"
        ).filter(run_test__agent_definition__isnull=False)
        if options["job"]:
            jobs = jobs.filter(id__in=options["job"])

        prompts: dict[Any, set[str]] = defaultdict(set)
        excerpts: dict[Any, set[str]] = defaultdict(set)
        agents = {}
        for job in jobs.iterator():
            full = _full_prompt(job)
            if not full:
                continue
            agent = job.run_test.agent_definition
            agents[agent.id] = agent
            prompts[agent.id].add(full)
            excerpt = _excerpt(job)
            if excerpt:
                excerpts[agent.id].add(_normalised(excerpt))

        totals = {"agents": 0, "versions": 0, "calls": 0, "skipped": 0}
        with transaction.atomic() if dry_run else nullcontext():
            for agent_id, agent in agents.items():
                if len(prompts[agent_id]) != 1:
                    totals["skipped"] += 1
                    self.stdout.write(
                        f"skip agent {agent_id}: its jobs carry different prompts"
                    )
                    continue
                full = next(iter(prompts[agent_id]))
                self._backfill_agent(agent, full, excerpts[agent_id], totals)
            if dry_run:
                transaction.set_rollback(True)

        summary = (
            f"Harness agent prompt backfill: {totals['agents']} agents, "
            f"{totals['versions']} versions, {totals['calls']} calls updated; "
            f"{totals['skipped']} agents skipped."
        )
        if dry_run:
            summary = f"DRY RUN: {summary}"
        self.stdout.write(self.style.SUCCESS(summary))

    def _backfill_agent(
        self, agent, full: str, excerpts: set[str], totals: dict
    ) -> None:
        if _is_shortened(agent.description, full, excerpts):
            agent.description = full
            agent.save(update_fields=["description", "updated_at"])
            totals["agents"] += 1

        for version in AgentVersion.no_workspace_objects.filter(agent_definition=agent):
            snapshot = dict(version.configuration_snapshot or {})
            changed = []
            if _is_shortened(str(snapshot.get("description") or ""), full, excerpts):
                snapshot["description"] = full
                version.configuration_snapshot = snapshot
                changed.append("configuration_snapshot")
            if _is_shortened(version.description or "", full, excerpts):
                version.description = full
                changed.append("description")
            if changed:
                version.save(update_fields=[*changed, "updated_at"])
                totals["versions"] += 1

        call_ids = CallExecution.no_workspace_objects.filter(
            test_execution__run_test__agent_definition=agent
        ).values_list("id", flat=True)
        for call_id in call_ids.iterator():
            with transaction.atomic():
                call = CallExecution.no_workspace_objects.select_for_update().get(
                    id=call_id
                )
                metadata = dict(call.call_metadata or {})
                keys = [
                    key
                    for key in PROMPT_METADATA_KEYS
                    if _is_shortened(str(metadata.get(key) or ""), full, excerpts)
                ]
                if keys:
                    metadata.update(dict.fromkeys(keys, full))
                    call.call_metadata = metadata
                    call.save(update_fields=["call_metadata", "updated_at"])
                    totals["calls"] += 1
