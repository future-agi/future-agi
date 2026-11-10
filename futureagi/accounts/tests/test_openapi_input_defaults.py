from accounts.serializers.workspace import UserListRequestSerializer
from tfc.tests.schema_default_helpers import assert_encoded_default


def test_user_list_sort_documents_encoded_empty_default():
    assert_encoded_default(UserListRequestSerializer, "sort", "[]")
