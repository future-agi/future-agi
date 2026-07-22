from uuid import uuid4

from django.http import QueryDict

from accounts.serializers.rbac import (
    InviteCreateSerializer,
    MemberListRequestSerializer,
    MemberRoleUpdateSerializer,
    WorkspaceMemberListRequestSerializer,
)
from tfc.constants.levels import Level


def _query_data(values):
    query = QueryDict("", mutable=True)
    for key, value in values.items():
        if isinstance(value, list):
            query.setlist(key, value)
        else:
            query[key] = value
    return query


def test_member_list_filters_use_canonical_repeated_query_params():
    serializer = MemberListRequestSerializer(
        data=_query_data({"filter_status": ["Active", "Pending"]})
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filter_status"] == ["Active", "Pending"]


def test_member_list_filters_reject_json_encoded_list_values():
    serializer = MemberListRequestSerializer(
        data=_query_data({"filter_status": '["Active"]'})
    )

    assert not serializer.is_valid()
    assert "filter_status" in serializer.errors


def test_member_list_sort_fields_are_backend_columns():
    serializer = MemberListRequestSerializer(data=_query_data({"sort": "-org_level"}))

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["sort"] == "-org_level"

    serializer = MemberListRequestSerializer(data=_query_data({"sort": "orgRole"}))

    assert not serializer.is_valid()
    assert "sort" in serializer.errors


def test_workspace_member_list_sort_fields_are_backend_columns():
    serializer = WorkspaceMemberListRequestSerializer(
        data=_query_data({"sort": "ws_level"})
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["sort"] == "ws_level"

    serializer = WorkspaceMemberListRequestSerializer(
        data=_query_data({"sort": "wsRole"})
    )

    assert not serializer.is_valid()
    assert "sort" in serializer.errors


def test_invite_workspace_access_rejects_unknown_nested_fields():
    serializer = InviteCreateSerializer(
        data={
            "emails": ["reviewer@example.com"],
            "org_level": Level.MEMBER,
            "workspace_access": [
                {
                    "workspace_id": str(uuid4()),
                    "level": Level.WORKSPACE_MEMBER,
                    "workspaceId": str(uuid4()),
                }
            ],
        }
    )

    assert not serializer.is_valid()
    assert serializer.errors["workspace_access"][0]["workspaceId"] == [
        "Unknown field."
    ]


def test_member_role_update_workspace_access_keeps_workspace_ids_json_safe():
    workspace_id = str(uuid4())
    serializer = MemberRoleUpdateSerializer(
        data={
            "user_id": str(uuid4()),
            "org_level": Level.MEMBER,
            "workspace_access": [{"workspace_id": workspace_id}],
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["workspace_access"] == [
        {"workspace_id": workspace_id}
    ]
