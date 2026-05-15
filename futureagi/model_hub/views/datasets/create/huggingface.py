import traceback
import uuid

import requests
import structlog
from django.db import transaction
from rest_framework.generics import CreateAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

logger = structlog.get_logger(__name__)
from analytics.utils import (
    MixpanelEvents,
    MixpanelTypes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.choices import (
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset, Row
from model_hub.serializers.develop_dataset import DatasetSerializer, UploadFileForm
from model_hub.utils.utils import (
    get_data_type_huggingface,
    load_hf_dataset_with_retries,
)
from model_hub.views.utils.hugginface import (
    get_huggingface_dataset_info,
    process_huggingface_dataset,
)
from model_hub.views.utils.utils import get_recommendations
from tfc.settings.settings import HUGGINGFACE_API_TOKEN
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.parse_errors import parse_serialized_errors
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from futureagi._ee_stubs import APICallStatusChoices, APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import ROW_LIMIT_REACHED_MESSAGE, log_and_deduct_cost_for_resource_request
except ImportError:
    from futureagi._ee_stubs import ROW_LIMIT_REACHED_MESSAGE, log_and_deduct_cost_for_resource_request


class GetHuggingFaceDatasetConfigView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def post(self, request, *args, **kwargs):
        try:
            # form = UploadFileForm(request.POST, request.FILES)
            dataset_path = request.data.get("dataset_path")
            organization_id = (
                getattr(self.request, "organization", None)
                or self.request.user.organization
            ).id

            if not dataset_path:
                return self._gm.bad_request(get_error_message("DATASET_PATH_MISSING"))

            if not organization_id:
                return self._gm.bad_request("Organization not found")

            try:
                dataset_info = get_huggingface_dataset_info(
                    dataset_path, organization_id
                )
                return self._gm.success_response(
                    {
                        "message": "Dataset configuration retrieved successfully",
                        "dataset_info": dataset_info,
                    }
                )
            except Exception as e:
                logger.exception(
                    f"huggingface: Error in fetching the dataset configurations: {str(e)}"
                )
                if str(e) == "501":
                    return self._gm.bad_request(
                        get_error_message("CONTAINS_ARBITRARY_CODE")
                    )
                return self._gm.bad_request(
                    get_error_message("FAILED_TO_FETCH_DATASET")
                )

        except Exception as e:
            logger.exception(
                f"huggingface: Error in fetching the dataset configurations: {str(e)}"
            )
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_DATASET_CONFIG")
            )


class CreateDatasetFromHuggingFaceView(CreateAPIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_huggingface_dataset_info(self, dataset_name, split_name):
        try:
            headers = {"Authorization": f"Bearer {HUGGINGFACE_API_TOKEN}"}
            API_URL = (
                f"https://datasets-server.huggingface.co/size?dataset={dataset_name}"
            )
            response = requests.get(API_URL, headers=headers, timeout=30)
            response.raise_for_status()
            splits = response.json().get("size").get("splits")
            try:
                split_info = next(
                    split_data
                    for split_data in splits
                    if split_data.get("split") == split_name
                )
                return split_info
            except StopIteration:
                logger.error(f"Split {split_name} not found in dataset {dataset_name}")
                return self._gm.internal_server_error_response(
                    get_error_message("FAILED_TO_LOAD_DATASET_FROM_HUGGINGFACE")
                )
        except Exception as e:
            logger.exception(
                f"huggingface: Error in fetching the dataset rows: {str(e)}"
            )
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_LOAD_DATASET_FROM_HUGGINGFACE")
            )

    def post(self, request, *args, **kwargs):
        try:
            call_log_row_entry = log_and_deduct_cost_for_resource_request(
                organization=getattr(request, "organization", None)
                or request.user.organization,
                api_call_type=APICallTypeChoices.DATASET_ADD.value,
                workspace=request.workspace,
            )
            if (
                call_log_row_entry is None
                or call_log_row_entry.status
                == APICallStatusChoices.RESOURCE_LIMIT.value
            ):
                return self._gm.too_many_requests(
                    get_error_message("DATASET_CREATE_LIMIT_REACHED")
                )
            call_log_row_entry.status = APICallStatusChoices.SUCCESS.value
            call_log_row_entry.save()

            form = UploadFileForm(request.POST, request.FILES)
            new_dataset_name = form.data.get("name")
            model_type = form.data.get("model_type")
            num_rows = form.data.get("num_rows")

            dataset_name = request.data.get("huggingface_dataset_name")
            config_name = request.data.get("huggingface_dataset_config")
            split = request.data.get("huggingface_dataset_split")

            if not dataset_name:
                return self._gm.bad_request(get_error_message("DATASET_NAME_MISSING"))

            from model_hub.validators.dataset_validators import (
                validate_dataset_name_unique,
            )

            try:
                validate_dataset_name_unique(
                    new_dataset_name,
                    getattr(request, "organization", None) or request.user.organization,
                )
            except Exception as validation_err:
                return self._gm.bad_request(str(validation_err.detail[0]))

            if num_rows:
                if int(num_rows) < 0:
                    return self._gm.bad_request(get_error_message("ROWS_NOT_POSITIVE"))

            try:
                file_name = dataset_name.replace(
                    "/", "_"
                )  # Convert path to valid filename
                new_dataset_name = new_dataset_name if new_dataset_name else file_name
            except Exception:
                traceback.print_exc()
                return self._gm.bad_request(
                    get_error_message("FAILED_TO_LOAD_DATASET_FROM_HUGGINGFACE")
                )

            # Creating Dataset
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            dataset_id = uuid.uuid4()
            dataset_serializer = DatasetSerializer(
                data={
                    "id": dataset_id,
                    "name": new_dataset_name,
                    "organization": organization.id,
                    "model_type": model_type,
                    "user": request.user.id,
                }
            )

            if dataset_serializer.is_valid():
                try:
                    dataset_info = self.get_huggingface_dataset_info(
                        dataset_name, split
                    )
                    rows_in_dataset = (
                        int(num_rows) if num_rows else int(dataset_info.get("num_rows"))
                    )
                    call_log_row = log_and_deduct_cost_for_resource_request(
                        organization,
                        api_call_type=APICallTypeChoices.ROW_ADD.value,
                        config={"total_rows": rows_in_dataset},
                        workspace=request.workspace,
                    )
                    if (
                        call_log_row is None
                        or call_log_row.status
                        == APICallStatusChoices.RESOURCE_LIMIT.value
                    ):
                        return self._gm.too_many_requests(ROW_LIMIT_REACHED_MESSAGE)
                    call_log_row.status = APICallStatusChoices.SUCCESS.value
                    call_log_row.save()

                    try:
                        first_row = load_hf_dataset_with_retries(
                            dataset_name,
                            config_name,
                            split,
                            str(
                                (
                                    getattr(request, "organization", None)
                                    or request.user.organization
                                ).id
                            ),
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
                    columns_to_create = []
                    column_order = []
                    column_config_updates = {}

                    for column_info in first_row["features"]:
                        try:
                            column_name = column_info["name"].strip()

                            data_type = get_data_type_huggingface(column_info)

                            # Create the column object
                            column = Column(
                                id=uuid.uuid4(),
                                name=column_name,
                                data_type=data_type,
                                status=StatusType.RUNNING.value,
                                source=SourceChoices.OTHERS.value,
                            )

                            columns_to_create.append(column)

                            column_config_updates[str(column.id)] = {
                                "is_visible": True,
                                "is_frozen": None,
                            }
                        except Exception as e:
                            logger.exception(
                                f"huggingface: Error in creating column: {str(e)}"
                            )
                            return self._gm.bad_request(
                                get_error_message(
                                    "FAILED_TO_CREATE_DATASET_FROM_HUGGINGFACE"
                                )
                            )

                    with transaction.atomic():
                        dataset = dataset_serializer.save()
                        for column in columns_to_create:
                            column.dataset = dataset
                        # Bulk create columns in the database
                        Column.objects.bulk_create(columns_to_create)

                        for column in columns_to_create:
                            column_order.append(str(column.id))

                        dataset.column_order = column_order
                        dataset.column_config = column_config_updates
                        dataset.save()

                    get_recommendations(dataset)

                    rows = {}

                    for index in range(rows_in_dataset):
                        rows[index] = str(
                            Row.objects.create(dataset=dataset, order=index).id
                        )

                    if request.headers.get("X-Api-Key") is not None:
                        properties = get_mixpanel_properties(
                            user=request.user,
                            dataset=dataset,
                            row_count=rows_in_dataset,
                            type=MixpanelTypes.HUGGINGFACE.value,
                        )
                        track_mixpanel_event(
                            MixpanelEvents.SDK_DATASET_CREATE.value, properties
                        )

                except Exception as e:
                    logger.exception(
                        f"huggingface: Error in creating the dataset from huggingface: {str(e)}"
                    )
                    return self._gm.bad_request(
                        get_error_message("FAILED_TO_CREATE_DATASET_FROM_HUGGINGFACE")
                    )
            else:
                return self._gm.bad_request(parse_serialized_errors(dataset_serializer))

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
                    str(
                        (
                            getattr(request, "organization", None)
                            or request.user.organization
                        ).id
                    ),
                    rows_in_dataset,
                    column_order,
                    rows,
                ),
                queue="tasks_l",
            )

            return self._gm.success_response(
                {
                    "message": "Dataset creation started successfully. Please check in some time",
                    "dataset_id": str(dataset.id),
                    "dataset_name": dataset.name,
                    "dataset_model_type": dataset.model_type,
                }
            )
        except Exception as e:
            traceback.print_exc()
            logger.exception(
                f"huggingface: Error in creating the dataset from huggingface: {str(e)}"
            )
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_DATASET_FROM_HUGGINGFACE")
            )
