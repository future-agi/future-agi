from uuid import uuid4

from django.http import QueryDict

from accounts.serializers.contracts import (
    TeamUsersResponseSerializer,
    WorkspaceInviteResponseSerializer,
    WorkspaceListPaginatedResponseSerializer,
    WorkspaceManagementListResponseSerializer,
)
from accounts.serializers.workspace import UserListRequestSerializer
from tfc.utils.api_contracts import _unknown_fields


def test_user_list_sort_accepts_plain_backend_field():
    serializer = UserListRequestSerializer(data={"sort": "-name"})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["sort"] == ["-name"]


def test_user_list_sort_accepts_json_table_sort_payload():
    serializer = UserListRequestSerializer(
        data={
            "sort": '[{"columnId":"role","type":"asc"},{"columnId":"email","type":"desc"}]'
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["sort"] == ["computed_role_rank", "-email"]


def test_user_list_sort_accepts_bracket_table_sort_payload_under_strict_unknown_check():
    query = QueryDict("", mutable=True)
    query["sort[0][columnId]"] = "startDate"
    query["sort[0][type]"] = "desc"
    serializer = UserListRequestSerializer(data=query)

    assert _unknown_fields(query, serializer) == []
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["sort"] == ["-created_at"]


def test_user_list_sort_keeps_multi_value_filters_when_normalizing_query_dict():
    query = QueryDict("", mutable=True)
    query.setlist("filter_status", ["Active", "Pending"])
    query.setlist("filter_role", ["workspace_admin", "workspace_member"])
    query["sort"] = "email"
    serializer = UserListRequestSerializer(data=query)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filter_status"] == ["Active", "Pending"]
    assert serializer.validated_data["filter_role"] == [
        "workspace_admin",
        "workspace_member",
    ]
    assert serializer.validated_data["sort"] == ["email"]


def test_workspace_list_response_serializer_matches_paginated_runtime_shape():
    admin_id = uuid4()
    workspace_id = uuid4()
    serializer = WorkspaceListPaginatedResponseSerializer(
        data={
            "count": 1,
            "next": None,
            "previous": None,
            "results": [
                {
                    "id": str(workspace_id),
                    "name": "Default Workspace",
                    "display_name": "Default Workspace",
                    "admin_names": [{"id": str(admin_id), "name": "Kartik"}],
                    "start_data": "2026-05-20",
                    "last_update_date": "2026-05-20",
                    "invite_link": "",
                    "user_ws_level": 8,
                    "user_ws_role": "Workspace Admin",
                }
            ],
            "total_pages": 1,
            "current_page": 1,
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_workspace_invite_response_serializer_matches_general_methods_envelope():
    workspace_id = uuid4()
    serializer = WorkspaceInviteResponseSerializer(
        data={
            "status": True,
            "result": {
                "results": [
                    {
                        "email": "annotator@example.com",
                        "status": "invited",
                        "workspaces": [str(workspace_id)],
                        "select_all": False,
                        "total_workspaces": 1,
                    }
                ],
                "total_invited": 1,
                "select_all": False,
                "total_workspaces": 1,
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_team_users_response_serializer_matches_manager_runtime_shape():
    user_id = uuid4()
    serializer = TeamUsersResponseSerializer(
        data={
            "status": True,
            "result": {
                "org_name": "Future AGI",
                "workspace_name": "Default Workspace",
                "results": [
                    {
                        "id": str(user_id),
                        "email": "member@example.com",
                        "name": "Member",
                        "organization_role": "member",
                        "created_at": "2026-05-20",
                        "status": "Active",
                        "role": "workspace_member",
                        "membership_type": "primary",
                        "workspace_role": "workspace_member",
                        "workspace_member": True,
                    }
                ],
                "total": 1,
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_workspace_management_response_serializer_matches_enveloped_list_shape():
    workspace_id = uuid4()
    serializer = WorkspaceManagementListResponseSerializer(
        data={
            "status": True,
            "result": {
                "organization": "Future AGI",
                "workspaces": [
                    {
                        "id": str(workspace_id),
                        "name": "Default Workspace",
                        "display_name": "Default Workspace",
                        "description": "",
                        "is_default": True,
                        "member_count": 3,
                        "created_at": "2026-05-20",
                        "created_by": "Kartik",
                    }
                ],
                "total": 1,
            },
        }
    )

    assert serializer.is_valid(), serializer.errors
