import pytest

from model_hub.serializers.contracts import (
    DatasetTableQuerySerializer,
    EvalApiLogTableQuerySerializer,
)
from tfc.tests.schema_default_helpers import assert_encoded_default


@pytest.mark.parametrize(
    "serializer_class", [DatasetTableQuerySerializer, EvalApiLogTableQuerySerializer]
)
def test_table_search_documents_encoded_empty_default(serializer_class):
    assert_encoded_default(serializer_class, "search", "{}")
