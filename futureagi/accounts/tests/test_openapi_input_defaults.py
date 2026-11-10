from urllib.parse import urlencode

import pytest
from django.http import QueryDict

from accounts.serializers.workspace import UserListRequestSerializer
from tfc.tests.schema_default_helpers import assert_encoded_default, field_schema
from tfc.utils.api_contracts import _unknown_fields


def test_user_list_sort_documents_encoded_empty_default():
    assert_encoded_default(UserListRequestSerializer, "sort", "")


@pytest.mark.parametrize(
    ("column", "direction", "expected"),
    [("email", "desc", ["-email"]), ("startDate", "asc", ["created_at"])],
)
def test_documented_sort_default_preserves_bracket_sort(column, direction, expected):
    schema = field_schema(UserListRequestSerializer().fields["sort"])
    bracket_sort = {"sort[0][columnId]": column, "sort[0][type]": direction}
    results = []
    for data in (bracket_sort, {**bracket_sort, "sort": schema["default"]}):
        query = QueryDict(urlencode(data))
        serializer = UserListRequestSerializer(data=query)
        assert _unknown_fields(query, serializer) == []
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["sort"] == expected
        results.append(serializer.validated_data)
    assert results[0] == results[1]
