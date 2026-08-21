import structlog
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from tfc.utils.base_viewset import BaseModelViewSetMixin
from sources.models.source_book import SourceBook
from sources.models.scan_page import ScanPage
from sources.models.target_passage import TargetPassage
from sources.serializers import (
    SourceBookSerializer,
    ScanPageSerializer,
    TargetPassageSerializer,
    ImportTranskribusSerializer,
)
from sources.services.transkribus_parser import TranskribusParserService

logger = structlog.get_logger(__name__)


def _get_org_and_workspace(request):
    org = getattr(request, "organization", None)
    if not org and hasattr(request, "user"):
        org = getattr(request.user, "organization", None)
    workspace = getattr(request, "workspace", None)
    return org, workspace


class SourceBookViewSet(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = SourceBookSerializer
    queryset = SourceBook.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        author = self.request.query_params.get("author")
        if author:
            queryset = queryset.filter(author__icontains=author)
        return queryset

    def perform_create(self, serializer):
        org, workspace = _get_org_and_workspace(self.request)
        serializer.save(organization=org, workspace=workspace)

    @action(detail=True, methods=["post"], url_path="import-transkribus")
    def import_transkribus(self, request, pk=None):
        book = self.get_object()
        serializer = ImportTranskribusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data["file"]
        org, workspace = _get_org_and_workspace(request)

        try:
            pages = TranskribusParserService.import_transkribus_zip(
                book_or_id=book,
                zip_file_path_or_obj=uploaded_file,
                organization=org,
                workspace=workspace,
            )
            return Response(
                {
                    "message": f"Successfully imported {len(pages)} pages from Transkribus export.",
                    "imported_pages_count": len(pages),
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.error("Failed to import Transkribus ZIP archive", error=str(e), exc_info=True)
            return Response(
                {"error": "Failed to import Transkribus ZIP archive. Please ensure the uploaded file is a valid PAGE-XML archive."},
                status=status.HTTP_400_BAD_REQUEST,
            )


class ScanPageViewSet(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ScanPageSerializer
    queryset = ScanPage.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        book_id = self.request.query_params.get("book_id")
        if book_id:
            queryset = queryset.filter(book_id=book_id)
        return queryset

    def perform_create(self, serializer):
        org, workspace = _get_org_and_workspace(self.request)
        serializer.save(organization=org, workspace=workspace)


class TargetPassageViewSet(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = TargetPassageSerializer
    queryset = TargetPassage.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        scan_page_id = self.request.query_params.get("scan_page_id")
        if scan_page_id:
            queryset = queryset.filter(scan_page_id=scan_page_id)
        return queryset

    def perform_create(self, serializer):
        org, workspace = _get_org_and_workspace(self.request)
        serializer.save(organization=org, workspace=workspace)

