import uuid

import structlog
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

logger = structlog.get_logger(__name__)
from analytics.utils import (
    MixpanelEvents,
    MixpanelTypes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.develop_dataset import Dataset
from model_hub.serializers.develop_dataset import DatasetSerializer
from model_hub.validators.dataset_validators import (
    validate_dataset_name_unique,
    validate_empty_dataset_row_bound,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.parse_errors import parse_serialized_errors
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from futureagi._ee_stubs import APICallStatusChoices, APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_resource_request
except ImportError:
    from futureagi._ee_stubs import log_and_deduct_cost_for_resource_request


class CreateEmptyDatasetView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

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

            dataset_name = request.data.get("new_dataset_name")
            model_type = request.data.get("model_type")
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            is_sdk = request.data.get("is_sdk", False)

            if not dataset_name:
                return self._gm.bad_request(
                    get_error_message("MISSING_NEW_DATASET_NAME")
                )

            # Enforce upper bound on row count (aligned with UI: max 10)
            row_count = request.data.get("row", None)
            if row_count is not None:
                try:
                    row_count = int(row_count)
                    validate_empty_dataset_row_bound(row_count)
                except (ValueError, TypeError):
                    pass
                except Exception as validation_err:
                    return self._gm.bad_request(str(validation_err.detail[0]))

            try:
                validate_dataset_name_unique(dataset_name, organization)
            except Exception as validation_err:
                return self._gm.bad_request(str(validation_err.detail[0]))

            dataset_id = uuid.uuid4()

            dataset_serializer = DatasetSerializer(
                data={
                    "id": dataset_id,
                    "name": dataset_name,
                    "organization": organization.id,
                    "model_type": model_type,
                    "user": request.user.id,
                }
            )

            if dataset_serializer.is_valid():
                dataset = dataset_serializer.save(
                    dataset_config={
                        "eval_recommendations": ["Deterministic Evals"],
                        "is_sdk": is_sdk,
                    }
                )

                if request.headers.get("X-Api-Key") is not None:
                    properties = get_mixpanel_properties(
                        type=MixpanelTypes.EMPTY.value,
                        user=request.user,
                        dataset=dataset,
                    )
                    track_mixpanel_event(
                        MixpanelEvents.SDK_DATASET_CREATE.value, properties
                    )

                return self._gm.success_response(
                    {
                        "message": "Empty dataset created successfully",
                        "dataset_id": str(dataset.id),
                        "dataset_name": dataset.name,
                        "dataset_model_type": model_type,
                    }
                )
            else:
                # print(serializers.errors)
                return self._gm.bad_request(parse_serialized_errors(dataset_serializer))

        except Exception as e:
            logger.exception(f"Error in creating the empty dataset: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_EMPTY_DATASET")
            )
