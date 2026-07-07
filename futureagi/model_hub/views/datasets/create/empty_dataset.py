import uuid

import structlog
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from analytics.utils import (
    MixpanelEvents,
    MixpanelTypes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    CreateEmptyDatasetRequestSerializer,
)
from model_hub.serializers.develop_dataset import DatasetSerializer
from model_hub.serializers.develop_dataset_contracts import (
    DatasetCreateStartedResponseSerializer,
)
from model_hub.validators.dataset_validators import (
    validate_dataset_name_unique,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.parse_errors import parse_serialized_errors
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.billing.boundary import get_billing

logger = structlog.get_logger(__name__)


class CreateEmptyDatasetView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    @validated_request(
        request_serializer=CreateEmptyDatasetRequestSerializer,
        responses={
            200: DatasetCreateStartedResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.validated_data
            dataset_name = data.get("new_dataset_name")
            model_type = data.get("model_type")
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            is_sdk = data.get("is_sdk", False)

            try:
                validate_dataset_name_unique(dataset_name, organization)
            except Exception as validation_err:
                return self._gm.bad_request(str(validation_err.detail[0]))

            billing = get_billing()
            call_log_row_entry = billing.log_and_deduct_resource(
                organization=organization,
                api_call_type=APICallTypeChoices.DATASET_ADD.value,
                workspace=request.workspace,
            )
            if (
                call_log_row_entry is not None
                and call_log_row_entry.status
                == APICallStatusChoices.RESOURCE_LIMIT.value
            ):
                return self._gm.too_many_requests(
                    get_error_message("DATASET_CREATE_LIMIT_REACHED")
                )
            if call_log_row_entry is not None:
                call_log_row_entry.status = APICallStatusChoices.SUCCESS.value
                call_log_row_entry.save()

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
                    workspace=getattr(request, "workspace", None),
                    dataset_config={
                        "eval_recommendations": ["Deterministic Evals"],
                        "is_sdk": is_sdk,
                    },
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
