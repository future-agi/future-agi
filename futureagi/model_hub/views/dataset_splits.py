"""Dataset-scoped live splits, atomic membership writes and bounded exports."""

import json

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.dataset_splits import DatasetSplit
from model_hub.models.develop_dataset import Cell, Column
from model_hub.serializers.dataset_splits import (
    DatasetSplitDeleteResponseSerializer,
    DatasetSplitExportQuerySerializer,
    DatasetSplitExportResponseSerializer,
    DatasetSplitListResponseSerializer,
    DatasetSplitMembershipSerializer,
    DatasetSplitPatchSerializer,
    DatasetSplitResponseSerializer,
    DatasetSplitRowQuerySerializer,
    DatasetSplitRowResponseSerializer,
    DatasetSplitUpdateResponseSerializer,
    DatasetSplitWriteSerializer,
)
from model_hub.services.dataset_splits import (
    change_membership,
    list_splits,
    live_rows,
    memberships,
    scoped_dataset,
    split_result,
    touch_dataset,
    update_saved_split_names,
)
from tfc.utils.api_contracts import validated_request


def success(result):
    return Response({"status": True, "result": result})


def duplicate_name():
    return Response(
        {"status": False, "message": "A split with that name already exists."},
        status=409,
    )


class DatasetSplitsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(responses={200: DatasetSplitListResponseSerializer})
    def get(self, request, dataset_id):
        return success(list_splits(scoped_dataset(request, dataset_id)))

    @validated_request(
        request_serializer=DatasetSplitWriteSerializer,
        responses={200: DatasetSplitResponseSerializer},
    )
    def post(self, request, dataset_id):
        try:
            with transaction.atomic():
                dataset = scoped_dataset(request, dataset_id, write=True, lock=True)
                if DatasetSplit.objects.filter(dataset=dataset).count() >= 100:
                    raise ValidationError("A dataset supports at most 100 splits.")
                split = DatasetSplit.objects.create(
                    dataset=dataset, **request.validated_data
                )
                touch_dataset(dataset)
                return success(split_result(split))
        except IntegrityError:
            return duplicate_name()


class DatasetSplitDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=DatasetSplitPatchSerializer,
        responses={200: DatasetSplitResponseSerializer},
    )
    def patch(self, request, dataset_id, split_id):
        try:
            with transaction.atomic():
                dataset = scoped_dataset(request, dataset_id, write=True, lock=True)
                split = get_object_or_404(
                    DatasetSplit.objects.filter(dataset=dataset), id=split_id
                )
                new_name = request.validated_data.get("name", split.name)
                if new_name != split.name:
                    update_saved_split_names(dataset, split.name, new_name)
                for key, value in request.validated_data.items():
                    setattr(split, key, value)
                split.save(update_fields=list(request.validated_data))
                touch_dataset(dataset)
                return success(split_result(split))
        except IntegrityError:
            return duplicate_name()

    @validated_request(responses={200: DatasetSplitDeleteResponseSerializer})
    def delete(self, request, dataset_id, split_id):
        with transaction.atomic():
            dataset = scoped_dataset(request, dataset_id, write=True, lock=True)
            split = get_object_or_404(
                DatasetSplit.objects.filter(dataset=dataset), id=split_id
            )
            update_saved_split_names(dataset, split.name)
            split.delete()
            touch_dataset(dataset)
        return success({"deleted": str(split_id)})


class DatasetSplitMembershipView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=DatasetSplitMembershipSerializer,
        responses={200: DatasetSplitUpdateResponseSerializer},
    )
    def post(self, request, dataset_id):
        return success(change_membership(request, dataset_id, request.validated_data))

    @validated_request(
        query_serializer=DatasetSplitRowQuerySerializer,
        responses={200: DatasetSplitRowResponseSerializer},
    )
    def get(self, request, dataset_id):
        dataset = scoped_dataset(request, dataset_id)
        row = get_object_or_404(
            live_rows(dataset), id=request.validated_query_data["row_id"]
        )
        ids = (
            memberships(dataset)
            .filter(row=row)
            .order_by("split_id")
            .values_list("split_id", flat=True)
        )
        return success(
            {"row_id": str(row.id), "split_ids": [str(value) for value in ids]}
        )


class SplitExportTooLarge(APIException):
    status_code = 413
    default_detail = "Export supports at most 5,000 rows, 100,000 cells and 16 MiB. Choose a smaller split."


class DatasetSplitExportView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=DatasetSplitExportQuerySerializer,
        responses={200: DatasetSplitExportResponseSerializer},
    )
    def get(self, request, dataset_id):
        with transaction.atomic():
            dataset = scoped_dataset(request, dataset_id)
            split = get_object_or_404(
                DatasetSplit.objects.filter(dataset=dataset),
                id=request.validated_query_data["split_id"],
            )
            row_ids = list(
                live_rows(dataset)
                .filter(
                    id__in=memberships(dataset).filter(split=split).values("row_id")
                )
                .order_by("order", "id")
                .values_list("id", flat=True)[:5001]
            )
            columns = dict(
                Column.no_workspace_objects.filter(
                    dataset=dataset, deleted=False
                ).values_list("id", "name")[:201]
            )
            if (
                len(row_ids) > 5000
                or len(columns) > 200
                or len(row_ids) * len(columns) > 100000
            ):
                raise SplitExportTooLarge()
            if len(set(columns.values())) != len(columns):
                raise ValidationError("Export requires unique column names.")
            fields = {row_id: dict.fromkeys(columns.values()) for row_id in row_ids}
            size = 0
            cells = Cell.no_workspace_objects.filter(
                dataset=dataset,
                row_id__in=row_ids,
                column_id__in=columns,
                deleted=False,
            )
            for row_id, column_id, value in cells.values_list(
                "row_id", "column_id", "value"
            ).iterator(chunk_size=500):
                size += len((value or "").encode("utf-8"))
                if size > 16 * 1024 * 1024:
                    raise SplitExportTooLarge()
                fields[row_id][columns[column_id]] = value
            payload = {
                "dataset_id": str(dataset.id),
                "split": split_result(split),
                "rows": [
                    {"row_id": str(row_id), "fields": fields[row_id]}
                    for row_id in row_ids
                ],
            }
            if len(json.dumps(payload).encode("utf-8")) > 16 * 1024 * 1024:
                raise SplitExportTooLarge()
            return success(payload)
