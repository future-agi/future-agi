"""Released queue clients retain bounded, ordered item pagination."""

import pytest
from django.http import QueryDict

from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.serializers.annotation_queues import QueueItemListQuerySerializer
from tfc.utils.pagination import MAX_PAGE_SIZE

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module", autouse=True)
def load_unscoped_urlconf():
    from django.urls import get_resolver
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    get_resolver().url_patterns


@pytest.fixture
def item_queue(organization, workspace, user):
    queue = AnnotationQueue.objects.create(
        name="Paging queue", organization=organization, workspace=workspace,
        created_by=user,
    )
    dataset = Dataset.objects.create(
        name="Paging data", organization=organization, workspace=workspace,
    )
    for index in range(5):
        QueueItem.objects.create(
            queue=queue, source_type="dataset_row",
            dataset_row=Row.objects.create(dataset=dataset, order=index),
            order=index, organization=organization, workspace=workspace,
        )
    return queue


def test_page_size_is_validated_without_changing_query_data():
    query = QueryDict("page_size=2&status=pending&status=completed")
    serializer = QueueItemListQuerySerializer(data=query)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["page_size"] == 2
    assert serializer.validated_data["status"] == ["pending", "completed"]
    assert query.getlist("status") == ["pending", "completed"]
    assert query["page_size"] == "2"


@pytest.mark.parametrize("size_key", ["page_size", "limit"])
def test_item_pages_honor_requested_size_and_page(auth_client, item_queue, size_key):
    pages = []
    for page in (1, 2, 3):
        response = auth_client.get(
            f"/model-hub/annotation-queues/{item_queue.id}/items/",
            {size_key: 2, "page": page, "ordering": "created_at"},
        )
        assert response.status_code == 200, response.data
        assert response.data["count"] == 5
        assert response.data["current_page"] == page
        assert response.data["total_pages"] == 3
        assert len(response.data["results"]) <= 2
        pages.append([str(row["id"]) for row in response.data["results"]])
    assert [len(page) for page in pages] == [2, 2, 1]
    expected = list(
        QueueItem.objects.filter(queue=item_queue)
        .order_by("created_at", "id").values_list("id", flat=True)
    )
    assert [item for page in pages for item in page] == [str(pk) for pk in expected]


@pytest.mark.parametrize("value", [0, -1, "invalid", MAX_PAGE_SIZE + 1])
def test_invalid_page_size_is_refused(auth_client, item_queue, value):
    response = auth_client.get(
        f"/model-hub/annotation-queues/{item_queue.id}/items/",
        {"page_size": value},
    )
    assert response.status_code == 400, response.data
    assert "page_size" in str(response.data)


def test_mixed_page_size_names_are_refused(auth_client, item_queue):
    response = auth_client.get(
        f"/model-hub/annotation-queues/{item_queue.id}/items/",
        {"page_size": 2, "limit": 3},
    )
    assert response.status_code == 400, response.data
    assert "Use either page_size or limit" in str(response.data)


def test_item_paging_keeps_other_query_keys_strict(auth_client, item_queue):
    response = auth_client.get(
        f"/model-hub/annotation-queues/{item_queue.id}/items/",
        {"page_size": 2, "unexpected": "value"},
    )
    assert response.status_code == 400, response.data
    assert "unexpected" in str(response.data)
    assert "Unknown field." in str(response.data)
