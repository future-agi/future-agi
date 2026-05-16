import json
import uuid
from pathlib import Path

from django.urls import reverse
from model_hub.models.choices import AnnotatorRole
from model_hub.serializers.annotation import AnnotationTaskSerializer
from model_hub.serializers.annotation_queues import (
    AddItemsSerializer,
    AnnotationQueueSerializer,
    AssignItemsSerializer,
    DiscussionCommentRequestSerializer,
    ReviewItemRequestSerializer,
    SelectionSerializer,
    SubmitAnnotationsSerializer,
)
from model_hub.serializers.monitor import MonitorSerializer
from model_hub.serializers.scores import (
    BulkCreateScoresSerializer,
    CreateScoreSerializer,
)
from tfc.utils.general_methods import GeneralMethods


def _uuid():
    return str(uuid.uuid4())


def _swagger():
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _body_ref(path, method):
    body_param = next(
        parameter
        for parameter in _swagger()["paths"][path][method]["parameters"]
        if parameter.get("in") == "body"
    )
    return body_param["schema"].get("$ref")


def _response_ref(path, method, status_code="200"):
    response = _swagger()["paths"][path][method]["responses"][status_code]
    return response.get("schema", {}).get("$ref")


def _query_params(path, method):
    return {
        parameter["name"]
        for parameter in _swagger()["paths"][path][method].get("parameters", [])
        if parameter.get("in") == "query"
    }


class TestAnnotationApiContract:
    def test_legacy_annotation_tasks_route_is_contract_visible(self):
        assert reverse("annotation-tasks-list") == "/model-hub/annotation-tasks/"
        assert "ai_model" in AnnotationTaskSerializer().fields
        assert "monitors" in AnnotationTaskSerializer().fields["ai_model"].fields
        assert not (
            hasattr(MonitorSerializer.Meta, "fields")
            and hasattr(MonitorSerializer.Meta, "exclude")
        )

    def test_queue_member_roles_accept_multiple_hats(self):
        serializer = AnnotationQueueSerializer()
        user_id = _uuid()

        normalized = serializer.validate_annotator_roles(
            {
                user_id: [
                    AnnotatorRole.MANAGER.value,
                    AnnotatorRole.ANNOTATOR.value,
                    AnnotatorRole.REVIEWER.value,
                ]
            }
        )
        assert set(normalized[user_id]) == {
            AnnotatorRole.MANAGER.value,
            AnnotatorRole.ANNOTATOR.value,
            AnnotatorRole.REVIEWER.value,
        }
        assert normalized[user_id][0] == AnnotatorRole.MANAGER.value

    def test_add_items_accepts_explicit_items_or_filter_selection_only(self):
        explicit = AddItemsSerializer(
            data={
                "items": [
                    {"source_type": "trace", "source_id": _uuid()},
                    {"source_type": "trace_session", "source_id": _uuid()},
                ]
            }
        )
        assert explicit.is_valid(), explicit.errors

        selection = AddItemsSerializer(
            data={
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": _uuid(),
                    "filter": [
                        {
                            "column_id": "latency_ms",
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "greater_than",
                                "filter_value": 100,
                                "col_type": "SYSTEM_METRIC",
                            },
                        }
                    ],
                    "exclude_ids": [_uuid()],
                }
            }
        )
        assert selection.is_valid(), selection.errors

        mixed = AddItemsSerializer(
            data={**explicit.initial_data, **selection.initial_data}
        )
        assert not mixed.is_valid()
        assert "Provide exactly one" in str(mixed.errors)

    def test_action_request_serializers_document_real_payloads(self):
        expected_refs = {
            ("/model-hub/annotation-queues/{queue_id}/items/add-items/", "post"): "#/definitions/AddItems",
            ("/model-hub/annotation-queues/{queue_id}/items/assign/", "post"): "#/definitions/AssignItems",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/", "post"): "#/definitions/SubmitAnnotations",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/", "post"): "#/definitions/DiscussionCommentRequest",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/review/", "post"): "#/definitions/ReviewItemRequest",
            ("/model-hub/scores/", "post"): "#/definitions/CreateScore",
            ("/model-hub/scores/bulk/", "post"): "#/definitions/BulkCreateScores",
            ("/tracer/observation-span/add_annotations/", "post"): "#/definitions/AddObservationSpanAnnotations",
            ("/tracer/trace/{id}/tags/", "patch"): "#/definitions/TraceTagsUpdate",
        }
        for (path, method), expected_ref in expected_refs.items():
            assert _body_ref(path, method) == expected_ref

    def test_custom_action_responses_document_general_methods_envelopes(self):
        expected_refs = {
            ("/model-hub/annotation-queues/{id}/progress/", "get"): "#/definitions/QueueProgressResponse",
            ("/model-hub/annotation-queues/{id}/hard-delete/", "post"): "#/definitions/QueueHardDeleteResponse",
            ("/model-hub/annotation-queues/{id}/export-to-dataset/", "post"): "#/definitions/QueueExportToDatasetResponse",
            ("/model-hub/annotation-queues/get-or-create-default/", "post"): "#/definitions/QueueDefaultResponse",
            ("/model-hub/annotation-queues/{queue_id}/items/add-items/", "post"): "#/definitions/QueueAddItemsResponse",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/annotations/submit/", "post"): "#/definitions/QueueSubmitAnnotationsResponse",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/complete/", "post"): "#/definitions/QueueNavigationResponse",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/skip/", "post"): "#/definitions/QueueNavigationResponse",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/release/", "post"): "#/definitions/QueueReleaseReservationResponse",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/", "get"): "#/definitions/QueueDiscussionResponse",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/", "post"): "#/definitions/QueueDiscussionResponse",
            ("/model-hub/annotation-queues/{queue_id}/items/{id}/review/", "post"): "#/definitions/QueueReviewItemResponse",
            ("/model-hub/scores/", "post"): "#/definitions/ScoreResponse",
            ("/model-hub/scores/bulk/", "post"): "#/definitions/BulkCreateScoresResponse",
            ("/model-hub/scores/for-source/", "get"): "#/definitions/ScoreForSourceResponse",
        }
        for (path, method), expected_ref in expected_refs.items():
            assert _response_ref(path, method) == expected_ref

        assert (
            _response_ref(
                "/model-hub/annotation-queues/{queue_id}/automation-rules/{id}/evaluate/",
                "post",
                "202",
            )
            == "#/definitions/AutomationRuleEvaluateAcceptedResponse"
        )

    def test_annotation_and_score_errors_document_uniform_envelopes(self):
        expected_error_refs = {
            (
                "/model-hub/annotation-queues/{queue_id}/items/add-items/",
                "post",
                "400",
            ): "#/definitions/ApiSelectionTooLargeError",
            (
                "/model-hub/annotation-queues/{queue_id}/items/add-items/",
                "post",
                "403",
            ): "#/definitions/ApiErrorResponse",
            (
                "/model-hub/annotation-queues/{id}/progress/",
                "get",
                "400",
            ): "#/definitions/ApiErrorResponse",
            (
                "/model-hub/annotation-queues/{queue_id}/items/{id}/discussion/",
                "post",
                "400",
            ): "#/definitions/ApiErrorResponse",
            (
                "/model-hub/annotation-queues/{queue_id}/items/{id}/review/",
                "post",
                "409",
            ): "#/definitions/ApiErrorResponse",
            ("/model-hub/scores/", "post", "400"): "#/definitions/ApiErrorResponse",
            (
                "/model-hub/scores/for-source/",
                "get",
                "500",
            ): "#/definitions/ApiErrorResponse",
        }
        for (path, method, status_code), expected_ref in expected_error_refs.items():
            assert _response_ref(path, method, status_code) == expected_ref

    def test_score_for_source_query_is_documented(self):
        assert {"source_type", "source_id"}.issubset(
            _query_params("/model-hub/scores/for-source/", "get")
        )

    def test_general_methods_error_envelope_is_uniform(self):
        gm = GeneralMethods()

        bad_request = gm.bad_request("Bad input")
        assert bad_request.data["status"] is False
        assert bad_request.data["result"] == "Bad input"
        assert bad_request.data["message"] == "Bad input"

        custom_error = gm.custom_error_response(409, "Already running")
        assert custom_error.data["status"] is False
        assert custom_error.data["result"] == "Already running"
        assert custom_error.data["message"] == "Already running"

    def test_selection_contract_rejects_unknown_source_type(self):
        serializer = SelectionSerializer(
            data={
                "mode": "filter",
                "source_type": "unknown",
                "project_id": _uuid(),
            }
        )
        assert not serializer.is_valid()
        assert "source_type" in serializer.errors

    def test_assign_items_contract_accepts_multi_assign_and_clear(self):
        item_id = _uuid()
        user_id = _uuid()

        assign = AssignItemsSerializer(
            data={
                "item_ids": [item_id],
                "user_ids": [user_id],
                "action": "add",
            }
        )
        assert assign.is_valid(), assign.errors

        clear = AssignItemsSerializer(
            data={"item_ids": [item_id], "user_ids": [], "action": "set"}
        )
        assert clear.is_valid(), clear.errors

    def test_discussion_and_review_request_contracts_validate_shape(self):
        empty_comment = DiscussionCommentRequestSerializer(data={"comment": ""})
        assert not empty_comment.is_valid()

        comment = DiscussionCommentRequestSerializer(
            data={
                "content": "Can you recheck @reviewer@example.com?",
                "mentioned_user_ids": [f"user:{_uuid()}"],
                "target_annotator_id": _uuid(),
            }
        )
        assert comment.is_valid(), comment.errors

        review = ReviewItemRequestSerializer(
            data={
                "action": "request_changes",
                "label_comments": [
                    {
                        "label_id": _uuid(),
                        "target_annotator_id": _uuid(),
                        "comment": "Wrong label value.",
                    }
                ],
            }
        )
        assert review.is_valid(), review.errors

        invalid_action = ReviewItemRequestSerializer(data={"action": "send_back"})
        assert not invalid_action.is_valid()
        assert "action" in invalid_action.errors

    def test_submit_annotations_requires_label_and_value(self):
        serializer = SubmitAnnotationsSerializer(
            data={
                "annotations": [{"label_id": _uuid(), "value": "yes"}],
                "item_notes": "whole item note",
            }
        )
        assert serializer.is_valid(), serializer.errors

        invalid = SubmitAnnotationsSerializer(data={"annotations": [{"value": "yes"}]})
        assert not invalid.is_valid()
        assert "annotations" in invalid.errors

    def test_score_write_contracts_include_queue_context_and_notes(self):
        create = CreateScoreSerializer(
            data={
                "source_type": "trace",
                "source_id": _uuid(),
                "label_id": _uuid(),
                "value": True,
                "notes": "label note",
                "queue_item_id": _uuid(),
            }
        )
        assert create.is_valid(), create.errors

        bulk = BulkCreateScoresSerializer(
            data={
                "source_type": "trace",
                "source_id": _uuid(),
                "scores": [{"label_id": _uuid(), "value": "positive"}],
                "notes": "label note",
                "span_notes": "whole item note",
                "span_notes_source_id": _uuid(),
                "queue_item_id": _uuid(),
            }
        )
        assert bulk.is_valid(), bulk.errors
