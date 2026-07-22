import traceback
import uuid

import structlog
from django.db.models import Max, Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
)
from model_hub.serializers.develop_dataset import SyntheticDataSerializer
from model_hub.serializers.develop_dataset_contracts import (
    DevelopDatasetMessageResponseSerializer,
)
from model_hub.tasks.develop_dataset import generate_new_columns, generate_new_rows
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _request_organization(request):
    return getattr(request, "organization", None) or request.user.organization


def _request_workspace_filter(request, field_name="workspace"):
    workspace = getattr(request, "workspace", None)
    if not workspace:
        return Q()

    if getattr(workspace, "is_default", False):
        return (
            Q(**{field_name: workspace})
            | Q(
                **{
                    f"{field_name}__is_default": True,
                    f"{field_name}__organization_id": workspace.organization_id,
                }
            )
            | Q(**{f"{field_name}__isnull": True})
        )

    return Q(**{field_name: workspace})


def _request_dataset_queryset(request):
    return Dataset.objects.filter(
        _request_workspace_filter(request),
        organization=_request_organization(request),
        deleted=False,
    )


class AddSyntheticData(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    # parser_classes = (MultiPartParser, FormParser, JSONParser)
    @validated_request(
        request_serializer=SyntheticDataSerializer,
        responses={
            200: DevelopDatasetMessageResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, dataset_id, *args, **kwargs):
        try:
            validated_data = request.validated_data
            dataset = _request_dataset_queryset(request).filter(id=dataset_id).first()
            if not dataset:
                return self._gm.not_found(get_error_message("DATASET_NOT_FOUND"))

            if validated_data["num_rows"] < 10:
                return self._gm.bad_request(get_error_message("10_ROWS_REQUIRED"))

            columns = validated_data["columns"]
            gen_columns = [col for col in columns if not col.get("skip", False)]
            new_columns = [col for col in columns if col.get("is_new", False)]
            gen_new_columns = [col for col in gen_columns if col.get("is_new", False)]

            # Generate New Columns
            generated_columns = []
            column_order = dataset.column_order
            for column in new_columns:
                if Column.objects.filter(
                    name=column["name"], dataset=dataset, deleted=False
                ).exists():
                    return self._gm.bad_request(
                        get_error_message("COLUMN_EXIST_IN_DATASET")
                    )
                new_column = Column.objects.create(
                    name=column["name"],
                    data_type=column["data_type"],
                    source=SourceChoices.OTHERS.value,
                    dataset=dataset,
                    status=StatusType.RUNNING.value,
                )
                # Update column order
                column_order.append(str(new_column.id))
                generated_columns.append(new_column)

            dataset.column_order = column_order
            dataset.save()

            rows = Row.objects.filter(dataset=dataset, deleted=False)
            row_ids = list(rows.values_list("id", flat=True))

            max_order = rows.aggregate(Max("order"))["order__max"] or -1

            if validated_data["fill_existing_rows"]:
                if len(gen_new_columns) >= 1:
                    for row in rows:
                        for col in generated_columns:
                            Cell.objects.create(
                                id=uuid.uuid4(),
                                dataset=dataset,
                                column=col,
                                row=row,
                                value=None,
                                status=CellStatus.RUNNING.value,
                            )

                    generate_new_columns.delay(
                        dataset_id=dataset_id,
                        row_ids=row_ids,
                        validated_data=validated_data,
                        new_columns_required_info=gen_new_columns,
                        new_column_db_model_ids=[c.id for c in generated_columns],
                        gen_columns=gen_columns,
                        max_order=max_order,
                    )

                elif len(gen_new_columns) < 1 and len(generated_columns) >= 1:
                    for row in rows:
                        for col in generated_columns:
                            _, created = Cell.objects.get_or_create(
                                dataset=dataset,
                                column=col,
                                row=row,
                                defaults={"id": uuid.uuid4(), "value": ""},
                            )

            total_columns = Column.objects.filter(dataset=dataset, deleted=False).exclude(
                source__in=[
                    SourceChoices.EXPERIMENT.value,
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                ]
            )
            total_columns.update(status=StatusType.RUNNING.value)
            new_rows_id = []
            for i in range(validated_data["num_rows"]):
                new_row = Row.objects.create(
                    id=str(uuid.uuid4()), dataset=dataset, order=max_order + 1 + i
                )
                new_rows_id.append(new_row.id)
                for col in total_columns:
                    Cell.objects.create(
                        id=uuid.uuid4(),
                        dataset=dataset,
                        column=col,
                        row=new_row,
                        value=None,
                        status=CellStatus.RUNNING.value,
                    )

            generate_new_rows.delay(dataset_id, validated_data, gen_columns, new_rows_id)

            return self._gm.success_response("Data Generation Started")

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in adding synthetic dataset: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_ADD_SYNTHETIC_DATA")
            )
