from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.authentication import viewer_denied
from simulate.serializers.harness_source_review import (
    HarnessSourceReviewAcceptSerializer,
    HarnessSourceReviewCancelSerializer,
    HarnessSourceReviewCreateSerializer,
    HarnessSourceReviewReadSerializer,
    HarnessSourceReviewReprepareSerializer,
)
from simulate.services import harness_source_reviews
from simulate.services.hosted_harness import HostedHarnessError
from tfc.utils.api_contracts import validated_request


class HarnessSourceReviewViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        from simulate.services.harness_provider import _usage_limit_response

        limit = _usage_limit_response(exc)
        if limit is not None:
            return limit
        if isinstance(exc, HostedHarnessError):
            return Response(
                exc.as_dict(), status=exc.status_code, headers=exc.headers()
            )
        return super().handle_exception(exc)

    @viewer_denied("prepare environment sources")
    @validated_request(
        request_serializer=HarnessSourceReviewCreateSerializer,
        responses={201: HarnessSourceReviewReadSerializer},
        reject_unknown_fields=True,
    )
    def create(self, request):
        return Response(
            harness_source_reviews.create_review(request, **request.validated_data),
            status=status.HTTP_201_CREATED,
        )

    @validated_request(responses={200: HarnessSourceReviewReadSerializer})
    def retrieve(self, request, pk=None):
        return Response(harness_source_reviews.get_review(request, pk))

    @viewer_denied("repair environment source review")
    @validated_request(
        request_serializer=HarnessSourceReviewReprepareSerializer,
        responses={200: HarnessSourceReviewReadSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def reprepare(self, request, pk=None):
        return Response(
            harness_source_reviews.reprepare_review(
                request, pk, **request.validated_data
            )
        )

    @viewer_denied("accept environment sources")
    @validated_request(
        request_serializer=HarnessSourceReviewAcceptSerializer,
        responses={200: HarnessSourceReviewReadSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return Response(
            harness_source_reviews.accept_review(request, pk, **request.validated_data)
        )

    @viewer_denied("cancel environment source review")
    @validated_request(
        request_serializer=HarnessSourceReviewCancelSerializer,
        responses={200: HarnessSourceReviewReadSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return Response(
            harness_source_reviews.cancel_review(request, pk, **request.validated_data)
        )
