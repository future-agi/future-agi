"""PAGE-CAP-DSX: every serializer and manual page-size alias rejects, never clamps."""

from unittest.mock import patch

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from model_hub.models.prompt_base_template import PromptBaseTemplate
from model_hub.serializers.annotation_queues import AnnotationQueueListQuerySerializer
from model_hub.serializers.catalog_queries import (
    EvalGroupListQuerySerializer,
    OptimizationListQuerySerializer,
)
from model_hub.serializers.contracts import (
    LegacyEvalTemplatesRequestSerializer,
    LegacyKnowledgeBaseFilesRequestSerializer,
    LegacyKnowledgeBaseTableQuerySerializer,
    OptimizeDatasetListQuerySerializer,
    OptimizeDatasetPageRequestSerializer,
    VectorDBColumnRequestSerializer,
)
from model_hub.serializers.develop_dataset import CompareDatasetSerializer
from model_hub.serializers.prompt_requests import PromptVersionsQuerySerializer
from model_hub.tests import test_pagination_api_exceptions
from model_hub.tests.network_isolation import (
    no_external_lookups as no_external_lookups,
)
from model_hub.views.annotation_queues import AnnotationQueuePagination
from model_hub.views.prompt_base_template import PromptBaseTemplateViewSet
from tfc.utils.api_contracts import validated_request
from tfc.utils.pagination import MAX_PAGE_SIZE, PageSizeTooLarge

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]
call_page_site = test_pagination_api_exceptions.call_page_site

EXAMPLE_ID = "00000000-0000-4000-8000-000000000001"
SERIALIZER_CASES = [
    (AnnotationQueueListQuerySerializer, "page_size", {}),
    (
        CompareDatasetSerializer,
        "page_size",
        {"base_column_name": "input", "dataset_ids": [EXAMPLE_ID]},
    ),
    (PromptVersionsQuerySerializer, "limit", {}),
    (LegacyKnowledgeBaseFilesRequestSerializer, "page_size", {"kb_id": EXAMPLE_ID}),
    (LegacyKnowledgeBaseTableQuerySerializer, "page_size", {}),
    (OptimizeDatasetListQuerySerializer, "limit", {}),
    (OptimizeDatasetPageRequestSerializer, "limit", {}),
    (
        VectorDBColumnRequestSerializer,
        "limit",
        {"column_id": EXAMPLE_ID, "sub_type": "test", "api_key": "test-placeholder"},
    ),
    (LegacyEvalTemplatesRequestSerializer, "page_size", {}),
    (EvalGroupListQuerySerializer, "page_size", {}),
    (OptimizationListQuerySerializer, "limit", {}),
]
SERIALIZER_IDS = [case[0].__name__ for case in SERIALIZER_CASES]


def _validate_through_dispatch(serializer_class, data):
    class ContractProbe(APIView):
        authentication_classes = []
        permission_classes = []

        @validated_request(request_serializer=serializer_class)
        def post(self, request):
            return Response(request.validated_data)

    request = APIRequestFactory().post("/page-cap-contract/", data, format="json")
    return ContractProbe.as_view()(request)


def _assert_cap_error(response):
    assert response.status_code == 400, response.data
    assert response.data["code"] == "page_size_too_large"
    assert PageSizeTooLarge.default_detail in response.data["message"]


@pytest.mark.parametrize(
    "serializer_class, field, payload", SERIALIZER_CASES, ids=SERIALIZER_IDS
)
def test_serializer_over_cap_is_typed_400(serializer_class, field, payload):
    response = _validate_through_dispatch(
        serializer_class, {**payload, field: MAX_PAGE_SIZE + 1}
    )

    _assert_cap_error(response)


@pytest.mark.parametrize(
    "serializer_class, field, payload", SERIALIZER_CASES, ids=SERIALIZER_IDS
)
def test_serializer_at_cap_succeeds_without_clamping(serializer_class, field, payload):
    response = _validate_through_dispatch(
        serializer_class, {**payload, field: MAX_PAGE_SIZE}
    )

    assert response.status_code == 200, response.data
    assert response.data[field] == MAX_PAGE_SIZE


@pytest.mark.parametrize(
    "serializer_class, field, payload", SERIALIZER_CASES, ids=SERIALIZER_IDS
)
def test_serializer_schema_declares_cap(serializer_class, field, payload):
    serializer = serializer_class(data=payload)
    assert serializer.fields[field].max_value == MAX_PAGE_SIZE


@pytest.mark.parametrize("site", ["prompt-executions", "combined-prompts"])
@pytest.mark.parametrize(
    "with_limit", [False, True], ids=["alias-only", "alias-and-limit"]
)
def test_prompt_page_size_alias_rejected_before_loading(
    call_page_site, site, with_limit
):
    query = {"page_size": str(MAX_PAGE_SIZE + 1)}
    if with_limit:
        query["limit"] = "1"
    response, lookup, combined = call_page_site(site, query)

    _assert_cap_error(response)
    lookup.assert_not_called()
    combined.assert_not_called()


def test_combined_limit_rejected_before_materializing(call_page_site):
    response, lookup, combined = call_page_site(
        "combined-prompts", {"limit": str(MAX_PAGE_SIZE + 1)}
    )

    _assert_cap_error(response)
    lookup.assert_not_called()
    combined.assert_not_called()


@pytest.mark.parametrize("site", ["prompt-executions", "combined-prompts"])
def test_prompt_page_size_alias_at_cap_succeeds(call_page_site, site):
    response, _, _ = call_page_site(site, {"page_size": str(MAX_PAGE_SIZE)})

    assert response.status_code == 200, response.data


@pytest.mark.parametrize(
    "size", [MAX_PAGE_SIZE, MAX_PAGE_SIZE + 1], ids=["at-cap", "over-cap"]
)
def test_prompt_base_template_manual_slice_cap(organization, workspace, user, size):
    request = APIRequestFactory().get(
        "/prompt-base-templates/", {"page_size": str(size)}
    )
    request.organization = organization
    request.workspace = workspace
    force_authenticate(request, user=user)
    with patch.object(
        PromptBaseTemplateViewSet,
        "get_queryset",
        return_value=PromptBaseTemplate.no_workspace_objects.none(),
    ) as lookup:
        response = PromptBaseTemplateViewSet.as_view({"get": "list"})(request)

    if size > MAX_PAGE_SIZE:
        _assert_cap_error(response)
        lookup.assert_not_called()
    else:
        assert response.status_code == 200, response.data
        assert response.data["result"]["total_count"] == 0
        lookup.assert_called_once()


@pytest.mark.parametrize(
    "size", [MAX_PAGE_SIZE, MAX_PAGE_SIZE + 1], ids=["at-cap", "over-cap"]
)
def test_annotation_queue_validated_alias_has_its_own_cap(size):
    class AliasProbe(APIView):
        authentication_classes = []
        permission_classes = []

        def get(self, request):
            # Exercise the manual consumer even if a caller supplies prevalidated
            # data without going through AnnotationQueueListQuerySerializer.
            request.validated_query_data = {"page_size": size}
            return Response(
                {"page_size": AnnotationQueuePagination().get_page_size(request)}
            )

    response = AliasProbe.as_view()(APIRequestFactory().get("/queue-page-size/"))
    if size > MAX_PAGE_SIZE:
        _assert_cap_error(response)
    else:
        assert response.status_code == 200, response.data
        assert response.data["page_size"] == MAX_PAGE_SIZE
