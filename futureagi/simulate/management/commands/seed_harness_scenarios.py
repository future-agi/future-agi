"""Seed hosted-harness template agents with their persona-scenarios (TH-7808).

Loads ``seeds/<slug>/scenarios.json`` for one or all seed template agents and
provisions them into a dev organization exactly the way the current simulation
flow does: one COMPLETED persona-dataset ``Scenarios`` row per seeded scenario
(via :func:`simulate.services.alk_simulate_ingestion.provision_alk_sim_run_test`),
attached to an ``AgentDefinition`` named after the template and listed on a
``RunTest`` — the shape the simulations page renders and the rerun flow
re-executes. Editing seeded scenarios is intentionally out of scope this phase.

    python manage.py seed_harness_scenarios --all --org <org_id>
    python manage.py seed_harness_scenarios --slug debt_collection --org <org_id>
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organization, Workspace
from simulate.harness_templates import catalog
from simulate.services.alk_simulate_ingestion import provision_alk_sim_run_test


class Command(BaseCommand):
    help = (
        "Load seeded persona-scenarios for hosted-harness template agents into a "
        "dev organization (TH-7808)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--slug", help="Seed one template by slug; omit with --all.")
        parser.add_argument(
            "--all", action="store_true", help="Seed every exposed template."
        )
        parser.add_argument(
            "--org", help="Organization id (default: the only org, else required)."
        )
        parser.add_argument(
            "--workspace",
            default=None,
            help="Workspace id (default: the org's first workspace).",
        )
        parser.add_argument(
            "--name",
            default=None,
            help="RunTest name (default: '<display_name> · seed suite').",
        )

    def handle(self, *args, **opts):
        slugs = self._resolve_slugs(opts)
        org = self._resolve_org(opts.get("org"))
        workspace = self._resolve_workspace(org, opts.get("workspace"))

        for slug in slugs:
            template = catalog.get_template(slug)
            if template is None:
                raise CommandError(f"unknown template: {slug}")
            scenarios = catalog.load_seed_scenarios(slug)
            if not scenarios:
                self.stdout.write(
                    self.style.WARNING(f"{slug}: no seeded scenarios — skipped")
                )
                continue
            name = opts.get("name") or f"{template['display_name']} · seed suite"
            run_test, created, agent_definition = provision_alk_sim_run_test(
                org,
                workspace=workspace,
                name=name,
                personas=scenarios,
                agent_name=template["display_name"],
                description=template.get("description") or "",
                modality=template.get("channel") or "voice",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{slug}: RunTest {run_test.id} · agent {agent_definition.id} · "
                    f"{len(created)} scenarios"
                )
            )

    def _resolve_slugs(self, opts) -> list[str]:
        if opts.get("all"):
            if opts.get("slug"):
                raise CommandError("pass either --slug or --all, not both")
            return [template["slug"] for template in catalog.list_templates()]
        slug = opts.get("slug")
        if not slug:
            raise CommandError("pass --slug <slug> or --all")
        return [slug]

    def _resolve_org(self, org_id):
        if org_id:
            try:
                return Organization.objects.get(id=org_id)
            except Organization.DoesNotExist as exc:
                raise CommandError(f"organization not found: {org_id}") from exc
        orgs = list(Organization.objects.all()[:2])
        if len(orgs) == 1:
            return orgs[0]
        raise CommandError("multiple organizations exist; pass --org <org_id>")

    def _resolve_workspace(self, org, workspace_id):
        if workspace_id:
            try:
                return Workspace.objects.get(id=workspace_id, organization=org)
            except Workspace.DoesNotExist as exc:
                raise CommandError(
                    f"workspace not found in org: {workspace_id}"
                ) from exc
        return (
            Workspace.objects.filter(organization=org).order_by("created_at").first()
        )
