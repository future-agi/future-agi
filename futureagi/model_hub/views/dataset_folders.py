"""Dataset folder reads and workspace-authorized mutations."""

from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.dataset_folders import (
    FolderListResponseSerializer,
    FolderMoveDatasetsSerializer,
    FolderMoveResponseSerializer,
    FolderPathSerializer,
    FolderResponseSerializer,
)
from model_hub.serializers.develop_dataset_contracts import (
    DatasetStarRequestSerializer,
    DevelopDatasetMessageResponseSerializer,
)
from model_hub.services import dataset_folders as folders
from tfc.utils.api_contracts import validated_request


class DatasetFoldersView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        responses={200: FolderListResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def get(self, request):
        return Response({"status": True, "result": folders.folder_inventory(request)})

    @validated_request(
        request_serializer=FolderPathSerializer,
        responses={201: FolderResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    @transaction.atomic
    def post(self, request):
        folder = folders.create_folder(request, request.validated_data["path"])
        return Response({"status": True, "result": folders.item(folder)}, status=201)


class DatasetFolderView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        responses={200: FolderResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def get(self, request, folder_id):
        result = folders.folder_detail(request, folder_id)
        return Response({"status": True, "result": result})

    @validated_request(
        request_serializer=FolderPathSerializer,
        responses={200: FolderResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    @transaction.atomic
    def patch(self, request, folder_id):
        folder = folders.relocate_folder(
            request, folder_id, request.validated_data["path"]
        )
        return Response(
            {"status": True, "result": folders.folder_detail(request, folder.id)}
        )

    @validated_request(
        request_serializer=DatasetStarRequestSerializer,
        responses={
            200: DevelopDatasetMessageResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @transaction.atomic
    def delete(self, request, folder_id):
        folders.remove_folder(request, folder_id)
        return Response({"status": True, "result": "Folder removed. Datasets kept."})


class MoveDatasetsToFolderView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=FolderMoveDatasetsSerializer,
        responses={200: FolderMoveResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    @transaction.atomic
    def post(self, request):
        data = request.validated_data
        result = folders.move_datasets(request, data["dataset_ids"], data["folder_id"])
        return Response({"status": True, "result": result})
