import traceback
import uuid

import structlog
from django.db import transaction
from django.db.models import Q
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

logger = structlog.get_logger(__name__)
from model_hub.models.choices import SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    HuggingFaceAddRowsRequestSerializer,
)
from model_hub.serializers.develop_dataset_contracts import (
    DatasetRowsImportMessageResponseSerializer,
)
from model_hub.utils.utils import (
    get_data_type_huggingface,
    load_hf_dataset_with_retries,
)
from model_hub.views.datasets.create.huggingface import CreateDatasetFromHuggingFaceView
from model_hub.views.utils.hugginface import process_huggingface_dataset
from tfc.middleware.workspace_context import get_current_workspace
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.billing.boundary import get_billing


def _request_organization(request):
    return getattr(request, "organization", None) or request.user.organization


def _request_workspace_filter(request, field_name="workspace"):
    workspace = getattr(request, "workspace", None) or get_current_workspace()
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


class AddRowsFromHuggingFaceView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    @validated_request(
        request_serializer=HuggingFaceAddRowsRequestSerializer,
        responses={
            200: DatasetRowsImportMessageResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, dataset_id, *args, **kwargs):
        try:
            data = request.validated_data
            num_rows = data.get("num_rows")
            dataset_name = data.get("huggingface_dataset_name")
            config_name = data.get("huggingface_dataset_config")
            split = data.get("huggingface_dataset_split")

            # Validate required fields (matching UI Zod schema)
            if not config_name or not str(config_name).strip():
                return self._gm.bad_request("HuggingFace dataset config is required")
            if not split or not str(split).strip():
                return self._gm.bad_request("HuggingFace dataset split is required")

            dataset = _request_dataset_queryset(request).filter(id=dataset_id).first()
            if not dataset:
                return self._gm.not_found(get_error_message("DATASET_NOT_FOUND"))
            organization = _request_organization(request)

            try:
                first_row = load_hf_dataset_with_retries(
                    dataset_name,
                    config_name,
                    split,
                    str(organization.id),
                    streaming=False,
                )
                if not first_row:
                    return self._gm.bad_request(
                        get_error_message("FAILED_TO_PREVIEW_DATASET")
                    )
            except Exception as e:
                logger.exception(
                    f"huggingface: Error in previewing the dataset: {str(e)}"
                )
                return self._gm.bad_request(
                    get_error_message("FAILED_TO_PREVIEW_DATASET")
                )

            # try:
            #     rows_in_dataset = data.shape[0]
            #     from ee.usage.utils.usage_entries import get_number_of_rows_allowed
            #     total_rows_allowed = get_number_of_rows_allowed(organization)
            #     if rows_in_dataset > total_rows_allowed:
            #         # crop the data to the total_rows_allowed
            #         data = data.head(total_rows_allowed)
            #         # return self._gm.bad_request(ROW_LIMIT_REACHED_MESSAGE)
            # except Exception as e:
            #     print("error in rows limit check : ", e)
            #     pass
            # --- Row Limit Check Start ---
            new_rows_count = (
                int(num_rows)
                if num_rows
                else int(
                    CreateDatasetFromHuggingFaceView()
                    .get_huggingface_dataset_info(dataset_name, split)
                    .get("num_rows", 0)
                )
            )
            existing_rows_count = Row.objects.filter(
                dataset=dataset, deleted=False
            ).count()
            prospective_total = existing_rows_count + new_rows_count
            billing = get_billing()
            call_log_row = billing.log_and_deduct_resource(
                organization=organization,
                api_call_type=APICallTypeChoices.ROW_ADD.value,
                config={"total_rows": prospective_total},
                workspace=request.workspace,
            )
            if billing.resource_denied(call_log_row):
                return self._gm.too_many_requests("Row limit reached")
            if call_log_row is not None:
                call_log_row.status = APICallStatusChoices.SUCCESS.value
                call_log_row.save()
            # --- Row Limit Check End ---

            # data.reset_index()

            column_order = dataset.column_order
            column_config = dataset.column_config or {}
            # Process Columns, Rows, and Cells
            for column_info in first_row["features"]:
                column_name = column_info["name"].strip()
                data_type = get_data_type_huggingface(column_info)
                try:
                    column = Column.objects.get(
                        name=column_name,
                        dataset=dataset,
                    )
                    column.status = StatusType.RUNNING.value
                    column.save()

                except Column.DoesNotExist:
                    column = Column.objects.create(
                        id=uuid.uuid4(),
                        name=column_name,
                        data_type=data_type,
                        source=SourceChoices.OTHERS.value,
                        dataset=dataset,
                        status=StatusType.RUNNING.value,
                    )

                    column_order.append(str(column.id))

                    column_config[str(column.id)] = {
                        "is_visible": True,
                        "is_frozen": None,
                    }

                    rows = Row.objects.filter(dataset=dataset, deleted=False)

                    batch_size = 1000
                    batch = []

                    for row in rows:
                        batch.append(
                            Cell(
                                id=uuid.uuid4(),
                                dataset=dataset,
                                column=column,
                                row=row,
                                value="",
                            )
                        )

                        if len(batch) >= batch_size:
                            with transaction.atomic():
                                Cell.objects.bulk_create(batch)
                            batch = []

                    if batch:
                        with transaction.atomic():
                            Cell.objects.bulk_create(batch)

                    dataset.column_order = column_order
                    dataset.column_config = column_config
                    dataset.save()

            # columns = Column.objects.filter(dataset=dataset)

            last_row = Row.all_objects.filter(dataset=dataset).order_by("-order").first()
            if last_row:
                max_order = last_row.order
            else:
                max_order = -1

            rows = {}
            for index in range(int(new_rows_count)):
                rows[index] = str(
                    Row.objects.create(dataset=dataset, order=max_order + 1 + index).id
                )

            # Start processing using Temporal
            # Import the activity to register it
            import tfc.temporal.background_tasks.activities  # noqa: F401
            from tfc.temporal.drop_in import start_activity

            start_activity(
                "process_huggingface_dataset_activity",
                args=(
                    str(dataset.id),
                    dataset_name,
                    config_name,
                    split,
                    str(organization.id),
                    new_rows_count,
                    column_order,
                    rows,
                ),
                queue="tasks_l",
            )
            # for index, row in data.iterrows():
            #     new_row = Row.objects.create(
            #         id=uuid.uuid4(),
            #         dataset=dataset,
            #         order=max_order + 1 + index
            #     )

            #     for column in columns:
            #         try:
            #             cell_value = str(row[column.name])
            #         except:
            #             cell_value = ""

            #         Cell.objects.create(
            #             id=uuid.uuid4(),
            #             dataset=dataset,
            #             column=column,
            #             row=new_row,
            #             value=cell_value,
            #         )

            return self._gm.success_response(
                {"message": f"{new_rows_count} Row(s) imported Succesfully"}
            )

        except Exception as e:
            traceback.print_exc()
            logger.exception(
                f"huggingface: Error in importing rows from huggingface dataset: {str(e)}"
            )
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_IMORT_ROWS_FROM_HUGGIGFACE_DATASET")
            )
