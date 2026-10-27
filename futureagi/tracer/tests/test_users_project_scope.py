"""TH-5037: every Users row names the project it belongs to.

The Users API lists one row per ``EndUser`` -- a user *within one project* --
not one row per person.  The same ``user_id`` active in two projects is two
rows, so each row carries its project's display name (table + CSV export).
No cross-project identity aggregation is introduced, and the names come from
the same authorized-project read the view already performs.
"""

import csv
import io
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.services.users_list_manager import (
    USERS_EXPORT_COLUMNS,
    UserCursorRead,
    UsersListManager,
)

pytestmark = [pytest.mark.integration, pytest.mark.api]

# Positions of the pre-existing CSV columns are a published contract.
_PRE_EXISTING_EXPORT_HEADER = [
    "User ID",
    "User ID Type",
    "User ID Hash",
    "First Active",
    "Last Active",
    "No. of Traces",
    "No. of Sessions",
    "Avg Session Duration (s)",
    "Total Tokens",
    "Total Cost ($)",
    "Avg Latency / Trace (ms)",
    "No. of LLM Calls",
    "Guardrails Triggered",
    "Evals Pass Rate (%)",
    "Input Tokens",
    "Output Tokens",
]

SUPPORT = str(uuid.UUID("11111111-1111-4111-8111-111111111111"))
SALES = str(uuid.UUID("22222222-2222-4222-8222-222222222222"))
OUTSIDE = str(uuid.UUID("33333333-3333-4333-8333-333333333333"))


def _user_row(project_id, *, user_id="user_123"):
    return {
        "user_id": user_id,
        "user_id_type": "email",
        "project_id": project_id,
        "end_user_id": uuid.uuid4(),
        "total_cost": 0.1,
        "num_traces": 3,
    }


def _cursor_read(rows):
    return UserCursorRead(
        payload={
            "table": rows,
            "has_more": False,
            "count_is_lower_bound": False,
            "query_complete": True,
            "query_exact": True,
            "ordering_exact": True,
            "approximate_fields": [],
        },
        window_start=datetime.utcnow(),
        window_end=datetime.utcnow(),
        checkpoint_order=None,
        seen_rows=len(rows),
        has_more=False,
        unseen_row_proven=False,
    )


def _manager(*, project_id=None, names=None):
    names = {SUPPORT: "Support Bot", SALES: "Sales Bot"} if names is None else names
    return UsersListManager(
        organization_id=str(uuid.uuid4()),
        allowed_project_ids=list(names),
        project_id=project_id,
        requested_columns=[],
        project_names=names,
    )


class TestUsersRowsNameTheirProject:
    """Manager level: labels come from the supplied authorized names only."""

    def test_same_user_in_two_projects_is_two_rows_each_naming_its_project(self):
        manager = _manager()
        rows = [_user_row(SUPPORT), _user_row(SALES)]
        with patch.object(
            UsersListManager, "_fetch_rows", return_value=(rows, 2, MagicMock())
        ):
            # No django_db mark: labelling must not read the database.
            payload = manager.list_payload(page_size=25, current_page=0)

        assert [(r["user_id"], r["project_name"]) for r in payload["table"]] == [
            ("user_123", "Support Bot"),
            ("user_123", "Sales Bot"),
        ]

    def test_never_names_a_project_outside_the_page_scope(self):
        # The request may see both projects, but this page is scoped to one;
        # a stray row from another project (or none) must not borrow a name.
        manager = UsersListManager(
            organization_id=str(uuid.uuid4()),
            allowed_project_ids=[SUPPORT, SALES],
            project_id=SUPPORT,
            project_names={SUPPORT: "Support Bot", SALES: "Sales Bot"},
        )
        rows = [
            _user_row(SUPPORT),
            _user_row(SALES),
            _user_row(OUTSIDE),
            _user_row(None),
        ]

        manager._label_project_scope(rows)

        assert [row["project_name"] for row in rows] == [
            "Support Bot",
            None,  # authorized, but outside this page's project scope
            None,  # never authorized
            None,  # no project on the row
        ]

    def test_names_for_unauthorized_projects_are_discarded(self):
        manager = UsersListManager(
            organization_id=str(uuid.uuid4()),
            allowed_project_ids=[SUPPORT],
            project_names={SUPPORT: "Support Bot", OUTSIDE: "Someone Else"},
        )
        assert manager.project_names == {SUPPORT: "Support Bot"}

    def test_without_names_rows_are_left_untouched(self):
        manager = UsersListManager(
            organization_id=str(uuid.uuid4()), allowed_project_ids=[SUPPORT]
        )
        rows = [_user_row(SUPPORT)]
        manager._label_project_scope(rows)
        assert "project_name" not in rows[0]

    def test_cursor_pages_name_their_projects(self):
        manager = _manager(project_id=SUPPORT)
        cursor_read = _cursor_read([_user_row(SUPPORT)])
        with patch.object(
            UsersListManager, "_read_cursor_page", return_value=cursor_read
        ) as read_page:
            result = manager.list_cursor_payload(page_size=25)

        read_page.assert_called_once_with(page_size=25, cursor=None, page_wall=True)
        assert result is cursor_read
        assert result.payload["table"][0]["project_name"] == "Support Bot"


@pytest.mark.django_db
class TestUsersEndpointNamesProjects:
    """HTTP level: the view's authorized-project read supplies the names."""

    @staticmethod
    def _project(organization, workspace, name, **extra):
        return Project.objects.create(
            name=name,
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            **extra,
        )

    def test_cross_project_page_names_each_row_and_hides_deleted_projects(
        self, auth_client, organization, workspace
    ):
        support = self._project(organization, workspace, "Support Bot")
        sales = self._project(organization, workspace, "Sales Bot")
        deleted = self._project(organization, workspace, "Deleted Bot", deleted=True)
        rows = [_user_row(support.id), _user_row(sales.id), _user_row(deleted.id)]
        with patch.object(
            UsersListManager, "_fetch_rows", return_value=(rows, 3, MagicMock())
        ):
            response = auth_client.get(
                "/tracer/users/",
                {
                    # No project_id: the cross-project /dashboard/users read.
                    "page_size": 25,
                    "current_page_index": 0,
                    # Explicit empty projection: no optional ClickHouse reads.
                    "requested_columns": "[]",
                },
            )

        assert response.status_code == status.HTTP_200_OK
        table = response.json()["result"]["table"]
        assert [(r["user_id"], r["project_name"]) for r in table] == [
            ("user_123", "Support Bot"),
            ("user_123", "Sales Bot"),
            ("user_123", None),  # soft-deleted project is not authorized
        ]
        assert [r["project_id"] for r in table] == [
            str(support.id),
            str(sales.id),
            str(deleted.id),
        ]

    def test_dashboard_projection_with_project_name_is_accepted(
        self, auth_client, organization, workspace
    ):
        # /dashboard/users now lists `project_name` among its visible columns,
        # so it rides in `requested_columns`; the API must accept it.
        support = self._project(organization, workspace, "Support Bot")
        with patch.object(
            UsersListManager,
            "_fetch_rows",
            return_value=([_user_row(support.id)], 1, MagicMock()),
        ):
            response = auth_client.get(
                "/tracer/users/",
                {
                    "page_size": 25,
                    "current_page_index": 0,
                    "requested_columns": '["user_id", "project_name"]',
                },
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["table"][0]["project_name"] == "Support Bot"

    def test_csv_export_names_each_row_through_the_cursor_wrapper(
        self, auth_client, organization, workspace
    ):
        # Real view export branch -> real list_cursor_payload wrapper -> real
        # iter_export_csv; only the ClickHouse page read is stubbed.
        support = self._project(organization, workspace, "Support Bot")
        sales = self._project(organization, workspace, "Sales Bot")
        page = _cursor_read([_user_row(support.id), _user_row(sales.id)])
        with patch.object(UsersListManager, "_read_cursor_page", return_value=page):
            response = auth_client.get("/tracer/users/", {"export": "true"})
            body = b"".join(response.streaming_content).decode("utf-8")

        assert response.status_code == status.HTTP_200_OK
        header, *rows = [r for r in csv.reader(io.StringIO(body)) if r]
        assert header[-1] == "Project"
        assert [(row[0], row[-1]) for row in rows] == [
            ("user_123", "Support Bot"),
            ("user_123", "Sales Bot"),
        ]


class TestUsersExportNamesProject:
    def test_export_appends_project_and_keeps_existing_column_positions(self):
        manager = _manager(project_id=SUPPORT)
        row = {**_user_row(SUPPORT), "project_name": "Support Bot"}

        body = "".join(manager.iter_export_csv(cursor_read=_cursor_read([row])))
        header, data = [r for r in csv.reader(io.StringIO(body)) if r]

        assert header == [*_PRE_EXISTING_EXPORT_HEADER, "Project"]
        assert header == [name for name, _ in USERS_EXPORT_COLUMNS]
        assert data[0] == "user_123"
        assert data[-1] == "Support Bot"

    def test_export_guards_a_formula_project_name(self):
        # Project names are customer-controlled text; the CSV-injection guard
        # must cover the new column like every other string cell.
        manager = _manager(project_id=SUPPORT)
        row = {**_user_row(SUPPORT), "project_name": '=HYPERLINK("https://x")'}

        body = "".join(manager.iter_export_csv(cursor_read=_cursor_read([row])))
        _header, data = [r for r in csv.reader(io.StringIO(body)) if r]

        assert data[-1] == '\'=HYPERLINK("https://x")'
