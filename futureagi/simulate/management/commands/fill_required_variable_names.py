"""
Fill the names an agent version must have values for, from its stored reading, on versions
read before the store step wrote them.

A version whose list is still unknown (null) and that has a stored contract gets the names
that contract requires, by the same rule the store step uses. A version that already has a
list, has no stored contract, or whose contract gives no answer (no process named), is
left as it is.

Usage:
    python manage.py fill_required_variable_names --dry-run
    python manage.py fill_required_variable_names
    python manage.py fill_required_variable_names --agent-version <agent_version_id> [...]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from simulate.models import HarnessAgentVersionRuntime
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services.agent_contracts import stored_version_contract
from simulate.services.harness_agent_versions import (
    NoAnswer,
    merge_required_names,
    reading_required_names,
)


class Command(BaseCommand):
    help = (
        "Write the names a version's stored reading requires onto versions whose list is "
        "still unknown, so a run warns of each one with no value."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be filled without writing it.",
        )
        parser.add_argument(
            "--agent-version",
            dest="agent_versions",
            action="append",
            default=[],
            help="Limit to these agent version ids (repeatable).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        with_contract = AgentVersionSetup.no_workspace_objects.exclude(
            contract_object_key=""
        ).values("agent_version_id")
        rows = HarnessAgentVersionRuntime.no_workspace_objects.filter(
            required_variable_names__isnull=True,
            agent_version_id__in=with_contract,
        )
        if options["agent_versions"]:
            rows = rows.filter(agent_version_id__in=options["agent_versions"])
        counts = {
            "filled": 0,
            "no_answer": 0,
            "unreadable": 0,
            "changed_meanwhile": 0,
        }
        for runtime_id, version_id in rows.values_list("id", "agent_version_id"):
            contract = stored_version_contract(version_id)
            if contract is None:
                counts["unreadable"] += 1
                self.stdout.write(f"{version_id}: contract unreadable, left unknown")
                continue
            with transaction.atomic():
                runtime = (
                    HarnessAgentVersionRuntime.no_workspace_objects.select_for_update()
                    .only("id", "agent_payload", "required_variable_names")
                    .get(id=runtime_id)
                )
                if runtime.required_variable_names is not None:
                    counts["changed_meanwhile"] += 1
                    continue
                names = reading_required_names(contract, runtime.agent_payload or {})
                if names is NoAnswer.NOT_GIVEN:
                    counts["no_answer"] += 1
                    self.stdout.write(
                        f"{version_id}: the reading named no process, left unknown"
                    )
                    continue
                if names is NoAnswer.UNREADABLE:
                    counts["unreadable"] += 1
                    self.stdout.write(
                        f"{version_id}: required names unreadable, left unknown"
                    )
                    continue
                required, own_payload = merge_required_names(
                    None, runtime.agent_payload, names
                )
                counts["filled"] += 1
                self.stdout.write(
                    f"{version_id}: {', '.join(required) or '(none required)'}"
                )
                if dry_run:
                    continue
                changes = {
                    "required_variable_names": required,
                    "updated_at": timezone.now(),
                }
                if own_payload is not None:
                    changes["agent_payload"] = own_payload
                HarnessAgentVersionRuntime.no_workspace_objects.filter(
                    id=runtime_id
                ).update(**changes)
        prefix = "Would fill" if dry_run else "Filled"
        self.stdout.write(
            f"{prefix} {counts['filled']} version(s); "
            f"{counts['no_answer']} with no answer; "
            f"{counts['unreadable']} unreadable; "
            f"{counts['changed_meanwhile']} changed meanwhile."
        )
