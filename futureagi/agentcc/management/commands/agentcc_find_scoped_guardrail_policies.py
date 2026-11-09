"""List, and with --repair --apply restore, guardrail policies left at key or
project scope (docs-agentcc-711, item 2).

POST guardrail-policies/{id}/apply/ used to set a policy's scope to the keys
or projects it named. The gateway receives only an org's global policies
(guardrail_sync), so such a policy stopped running for every key of its org,
the targeted ones included. apply now refuses (scoped_policy_not_supported);
this command finds the policies an earlier release left behind.

Read only by default: one line per policy, grouped by org. --repair shows
what a repair would change and changes nothing; --repair --apply sets each
listed policy's scope back to global (its applied keys and projects are kept
as a record and read by nothing). Each repair writes an organization audit
record in the same transaction; an unavailable audit rolls that repair back.
The gateway gets a restored policy at the org's next policy sync: add --sync
to push each repaired org now.
"""

import uuid
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models.audit_log import AuditScope
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.services.guardrail_sync import sync_guardrail_policies
from tfc.utils.audit import log_audit

SCOPED = (AgentccGuardrailPolicy.SCOPE_KEY, AgentccGuardrailPolicy.SCOPE_PROJECT)
AUDIT_ACTION = "agentcc.guardrail_policy_scope_repaired"


class Command(BaseCommand):
    help = (
        "List guardrail policies at key or project scope, per org: the gateway "
        "runs only global policies, so these protect no key. Read only by "
        "default; --repair shows the repair, --repair --apply restores scope "
        "global, and --sync then pushes each repaired org's policies."
    )

    def add_arguments(self, parser):
        parser.add_argument("--org", help="Only this organization's policies.")
        parser.add_argument(
            "--repair",
            action="store_true",
            help="Show the repair: scope back to global. Changes nothing alone.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="With --repair: set each listed policy's scope to global.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="With --repair --apply: push each repaired org's policies.",
        )

    def handle(self, *args, **options):
        if options["apply"] and not options["repair"]:
            raise CommandError("--apply only goes with --repair.")
        if options["sync"] and not options["apply"]:
            raise CommandError("--sync only goes with --repair --apply.")

        by_org = self._scoped_by_org(options["org"])
        if not by_org:
            self.stdout.write("No guardrail policy is at key or project scope.")
            return

        total = sum(len(policies) for policies in by_org.values())
        for org_id, policies in by_org.items():
            self.stdout.write(f"org {org_id}: {len(policies)} policy(ies)")
            for policy in policies:
                self.stdout.write("  " + _describe(policy))

        if not options["repair"]:
            self.stdout.write(
                f"{total} policy(ies) in {len(by_org)} org(s) protect no key. "
                "Run with --repair to see the repair."
            )
            return
        if not options["apply"]:
            self.stdout.write(
                f"Dry run: --repair --apply would set scope global on {total} "
                f"policy(ies) in {len(by_org)} org(s). Nothing was changed."
            )
            return

        repaired = 0
        repaired_orgs = {}
        try:
            for org_id, policies in by_org.items():
                for policy in policies:
                    if self._repair_policy(policy):
                        repaired += 1
                        repaired_orgs[org_id] = policy.organization
        except CommandError:
            self.stderr.write(
                f"Stopped after committing {repaired} policy repair(s) in "
                f"{len(repaired_orgs)} org(s). No immediate gateway sync was attempted."
            )
            raise
        self.stdout.write(
            f"Set scope global on {repaired} policy(ies) in {len(repaired_orgs)} org(s)."
        )
        if not options["sync"]:
            self.stdout.write(
                "The gateway runs them from each org's next policy sync. Call "
                "POST /agentcc/guardrail-policies/sync/ as an administrator of "
                "each repaired organization to push its policies now."
            )
            return
        for org_id, organization in repaired_orgs.items():
            synced = sync_guardrail_policies(organization)
            self.stdout.write(f"org {org_id}: gateway_synced={synced}")

    @staticmethod
    def _repair_policy(listed_policy):
        # Inventory is a snapshot. Lock and recheck so concurrent repairs cannot
        # overwrite a newer scope or create duplicate audit records.
        with transaction.atomic():
            policy = (
                AgentccGuardrailPolicy.no_workspace_objects.select_for_update()
                .filter(
                    pk=listed_policy.pk,
                    organization_id=listed_policy.organization_id,
                    scope__in=SCOPED,
                )
                .first()
            )
            if policy is None:
                return False
            old_scope = policy.scope
            policy.scope = AgentccGuardrailPolicy.SCOPE_GLOBAL
            policy.save(update_fields=["scope", "updated_at"])
            audit = log_audit(
                organization=listed_policy.organization,
                action=AUDIT_ACTION,
                scope=AuditScope.ORGANIZATION,
                target_id=policy.pk,
                changes={"scope": {"old": old_scope, "new": policy.scope}},
                # Never include policy names, checks, credentials or targets.
                metadata={"command": "agentcc_find_scoped_guardrail_policies"},
            )
            # The shared audit helper is best-effort. This repair is not: a
            # failed or muted audit must roll back the scope change as well.
            if audit is None:
                raise CommandError(
                    f"Audit record unavailable for policy {policy.pk}; "
                    "this policy's repair was rolled back."
                )
            return True

    @staticmethod
    def _scoped_by_org(org):
        policies = (
            AgentccGuardrailPolicy.no_workspace_objects.filter(
                deleted=False, scope__in=SCOPED
            )
            .select_related("organization")
            .order_by("organization_id", "priority", "name")
        )
        if org:
            try:
                policies = policies.filter(organization_id=uuid.UUID(org))
            except ValueError as exc:
                raise CommandError("--org must be an organization ID") from exc
        by_org = defaultdict(list)
        for policy in policies:
            by_org[str(policy.organization_id)].append(policy)
        return dict(by_org)


def _describe(policy):
    targets = (
        policy.applied_keys
        if policy.scope == AgentccGuardrailPolicy.SCOPE_KEY
        else policy.applied_projects
    )
    count = len(targets) if isinstance(targets, list) else 0
    state = "active" if policy.is_active else "inactive"
    return (
        f"{policy.id} {policy.name!r}: scope {policy.scope} "
        f"({count} {policy.scope}(s) named), {state}"
    )
