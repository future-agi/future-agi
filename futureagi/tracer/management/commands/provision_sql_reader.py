"""Print reviewed scope-specific ClickHouse DDL for self-hosted BI access."""

import os
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracer.models.project import Project
from tracer.sql_workbench.catalog import Scope
from tracer.sql_workbench.provision import direct_access_ddl
from tracer.sql_workbench.service import eval_config_scope


class Command(BaseCommand):
    help = "Print rolling-window, project-scoped readonly ClickHouse views/user/row policies. Does not execute DDL."

    def add_arguments(self, parser):
        parser.add_argument("--organization", type=UUID, required=True)
        parser.add_argument("--workspace", type=UUID, required=True)
        parser.add_argument("--project", type=UUID, required=True)
        parser.add_argument("--principal", required=True)
        parser.add_argument("--client-cidr", required=True)
        parser.add_argument("--window-days", type=int, default=31)

    def handle(self, *args, **options):
        project = Project.no_workspace_objects.filter(
            id=options["project"],
            organization_id=options["organization"],
            workspace_id=options["workspace"],
            workspace__is_active=True,
            workspace__deleted=False,
            trace_type="observe",
        ).first()
        if not project:
            raise CommandError(
                "Project must belong to that active organization and workspace."
            )
        end = timezone.now()
        try:
            configs, deleted = eval_config_scope(project)
            scope = Scope(
                project.organization_id,
                project.workspace_id,
                project.id,
                end - timedelta(days=options["window_days"]),
                end,
                configs,
                settings.CH25_EVAL_LOGGER_TABLE,
                deleted_eval_config_ids=deleted,
            )
            statements = direct_access_ddl(
                scope,
                database=settings.CLICKHOUSE_V2["CH25_DATABASE"],
                principal=options["principal"],
                password=os.environ.get("SQL_DIRECT_PASSWORD", ""),
                cidr=options["client_cidr"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            "-- Rolling-window access: expire/revoke this principal with your DB account lifecycle."
        )
        self.stdout.write(
            "-- Store securely: contains a credential hash. Apply with the operator's ClickHouse client."
        )
        for statement in statements:
            self.stdout.write(statement + ";")
