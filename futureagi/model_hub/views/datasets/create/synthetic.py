import traceback
import uuid

import structlog
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models import Organization

logger = structlog.get_logger(__name__)
from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.develop_dataset import (
    DatasetSerializer,
    SyntheticDatasetConfigSerializer,
    SyntheticDatasetCreationSerializer,
)
from model_hub.tasks.develop_dataset import (
    create_synthetic_dataset,
    generate_new_columns,
    generate_new_rows,
)
from model_hub.utils.synthetic_task_manager import SyntheticTaskManager
from model_hub.views.utils.synthetic_data import determine_data_type_syn_data
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.parse_errors import parse_serialized_errors
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    APICallStatusChoices = None
    APICallTypeChoices = None
try:
    from ee.usage.utils.usage_entries import ROW_LIMIT_REACHED_MESSAGE, log_and_deduct_cost_for_resource_request
except ImportError:
    ROW_LIMIT_REACHED_MESSAGE = None
    log_and_deduct_cost_for_resource_request = None


class CreateSyntheticDataset(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        from tfc.ee_gating import EEFeature, check_ee_feature

        org = getattr(request, "organization", None) or request.user.organization
        check_ee_feature(EEFeature.SYNTHETIC_DATA, org_id=str(org.id))

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

            serializer = SyntheticDatasetCreationSerializer(data=request.data)

            if serializer.is_valid():
                validated_data = serializer.validated_data
                dataset_name = validated_data["dataset"]["name"]
                organization = (
                    getattr(request, "organization", None) or request.user.organization
                )

                if Dataset.objects.filter(
                    name=dataset_name,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    deleted=False,
                ).exists():
                    return self._gm.bad_request(
                        get_error_message("DATASET_EXIST_IN_ORG")
                    )

                # if len(set(validated_data['columns']) != len(validated_data['columns'])):
                if len({col["name"] for col in validated_data["columns"]}) != len(
                    validated_data["columns"]
                ):
                    return self._gm.bad_request(
                        get_error_message("DUPLICATE_COLUMN_NAME")
                    )

                if validated_data["num_rows"] < 10:
                    return self._gm.bad_request(get_error_message("10_ROWS_REQUIRED"))

                # ------------------- Added Row Check -------------------
                call_log_row = log_and_deduct_cost_for_resource_request(
                    organization,
                    api_call_type=APICallTypeChoices.ROW_ADD.value,
                    config={"total_rows": validated_data["num_rows"]},
                    workspace=request.workspace,
                )
                if (
                    call_log_row is None
                    or call_log_row.status == APICallStatusChoices.RESOURCE_LIMIT.value
                ):
                    return self._gm.too_many_requests(ROW_LIMIT_REACHED_MESSAGE)
                call_log_row.status = APICallStatusChoices.SUCCESS.value
                call_log_row.save()
                # dataset = Dataset.objects.create(name=dataset_name)
                dataset_serializer = DatasetSerializer(
                    data={
                        "id": str(uuid.uuid4()),
                        "name": dataset_name,
                        "organization": organization.id,
                        "model_type": "GenerativeLLM",
                        "user": request.user.id,
                    }
                )

                if dataset_serializer.is_valid():
                    try:
                        dataset = dataset_serializer.save()

                        # Store the validated_data for future editing
                        config_data = validated_data.copy()
                        if config_data.get("kb_id"):
                            config_data["kb_id"] = str(config_data["kb_id"])

                        dataset.synthetic_dataset_config = config_data
                        dataset.save()

                        column_order = []
                        column_config = {}

                        for column_data in validated_data["columns"]:
                            # print("HERE IS COLD DATA:",column_data)
                            data_type = determine_data_type_syn_data(
                                column_data["data_type"]
                            )
                            # print("HERE IS COLD DATA:",data_type)

                            column = Column.objects.create(
                                id=uuid.uuid4(),
                                name=column_data["name"],
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

                        dataset.column_order = column_order
                        dataset.column_config = column_config
                        dataset.save()

                        # cell_ids =
                        for i in range(validated_data["num_rows"]):
                            new_row = Row.objects.create(
                                id=uuid.uuid4(), dataset=dataset, order=i
                            )
                            for column in column_order:
                                Cell.objects.create(
                                    id=uuid.uuid4(),
                                    dataset=dataset,
                                    column_id=column,
                                    row=new_row,
                                    value=None,
                                    status=CellStatus.RUNNING.value,
                                )

                        # If no request_uuid provided, try to get it from the task manager
                        task_manager = SyntheticTaskManager()

                        request_uuid = task_manager.start_task(str(dataset.id))

                        try:
                            create_synthetic_dataset.delay(
                                validated_data=validated_data,
                                dataset_id=dataset.id,
                                organization_id=organization.id,
                                creating_synthetic_dataset=True,
                                request_uuid=request_uuid,
                            )

                        except Exception:
                            logger.exception(
                                f" ===== Error : {traceback.format_exc()} ===== "
                            )

                        return self._gm.success_response(
                            {
                                "message": "Dataset creation started successfully. Please check in some time",
                                "data": dataset_serializer.data,
                            }
                        )

                    except Exception:
                        return self._gm.bad_request(
                            get_error_message("FAILED_TO_CREATE_SYNTHETIC_DATASET")
                        )
                else:
                    return self._gm.bad_request(
                        parse_serialized_errors(dataset_serializer)
                    )

            else:
                return self._gm.bad_request(parse_serialized_errors(serializer))

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in creating synthetic dataset: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_SYNTHETIC_DATASET")
            )


class GetSyntheticDatasetConfigView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, dataset_id, *args, **kwargs):
        try:
            # Get dataset and verify it exists and belongs to user's organization
            dataset = get_object_or_404(
                Dataset,
                id=dataset_id,
                deleted=False,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )

            # Check if this is a synthetic dataset (has synthetic_dataset_config)
            if not dataset.synthetic_dataset_config:
                return self._gm.bad_request(
                    get_error_message("NOT_A_SYNTHETIC_DATASET")
                )

            return self._gm.success_response(
                {
                    "message": "Synthetic dataset configuration retrieved successfully",
                    "data": dataset.synthetic_dataset_config,
                }
            )

        except Exception as e:
            logger.exception(f"Error in getting synthetic dataset config: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_SYNTHETIC_DATASET_CONFIG")
            )


class UpdateSyntheticDatasetConfigView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def put(self, request, dataset_id, *args, **kwargs):
        try:
            # Initialize task manager
            task_manager = SyntheticTaskManager()

            # Get dataset and verify it exists and belongs to user's organization
            dataset = get_object_or_404(
                Dataset,
                id=dataset_id,
                deleted=False,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )

            # Check if this is a synthetic dataset (has synthetic_dataset_config)
            if not dataset.synthetic_dataset_config:
                return self._gm.bad_request(
                    get_error_message("NOT_A_SYNTHETIC_DATASET")
                )

            if request.data.get("regenerate"):
                # Add regenerate flag when regenerate is true
                task_manager.operation_regenerate_key(str(dataset_id), "add")

                # Re-running full generation
                serializer = SyntheticDatasetConfigSerializer(data=request.data)
                if not serializer.is_valid():
                    return self._gm.bad_request(parse_serialized_errors(serializer))
                validated_data = serializer.validated_data

                # Clear and recreate everything
                Column.objects.filter(dataset=dataset).delete()
                Row.objects.filter(
                    dataset=dataset
                ).delete()  # This will cascade delete cells

                column_order = []
                column_config = {}
                for column_data in validated_data["columns"]:
                    data_type = determine_data_type_syn_data(column_data["data_type"])
                    column = Column.objects.create(
                        id=uuid.uuid4(),
                        name=column_data["name"],
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

                dataset.column_order = column_order
                dataset.column_config = column_config
                dataset.save()

                for i in range(validated_data["num_rows"]):
                    new_row = Row.objects.create(
                        id=uuid.uuid4(), dataset=dataset, order=i
                    )
                    for col_id in column_order:
                        Cell.objects.create(
                            id=uuid.uuid4(),
                            dataset=dataset,
                            column_id=col_id,
                            row=new_row,
                            value=None,
                            status=CellStatus.RUNNING.value,
                        )

                dataset.synthetic_dataset_config = validated_data
                dataset.save()

                request_uuid = task_manager.start_task(str(dataset.id))
                create_synthetic_dataset.delay(
                    validated_data=validated_data,
                    dataset_id=dataset.id,
                    organization_id=(
                        getattr(request, "organization", None)
                        or request.user.organization
                    ).id,
                    creating_synthetic_dataset=True,
                    request_uuid=request_uuid,
                )
                return self._gm.success_response(
                    {
                        "message": "Synthetic dataset configuration updated successfully. Data generation started.",
                        "data": {
                            "dataset_id": str(dataset.id),
                            "dataset_name": dataset.name,
                        },
                    }
                )

            else:
                # Remove regenerate flag when regenerate is false or not present
                task_manager.operation_regenerate_key(str(dataset_id), "remove")

            # Validate the new configuration
            serializer = SyntheticDatasetConfigSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(parse_serialized_errors(serializer))

            validated_data = serializer.validated_data

            # Check for duplicate column names
            if len({col["name"] for col in validated_data["columns"]}) != len(
                validated_data["columns"]
            ):
                return self._gm.bad_request(get_error_message("DUPLICATE_COLUMN_NAME"))

            # Check minimum rows requirement
            if validated_data["num_rows"] < 10:
                return self._gm.bad_request(get_error_message("10_ROWS_REQUIRED"))

            # Check row limit
            call_log_row = log_and_deduct_cost_for_resource_request(
                getattr(request, "organization", None) or request.user.organization,
                api_call_type=APICallTypeChoices.ROW_ADD.value,
                config={"total_rows": validated_data["num_rows"]},
                workspace=request.workspace,
            )
            if (
                call_log_row is None
                or call_log_row.status == APICallStatusChoices.RESOURCE_LIMIT.value
            ):
                return self._gm.too_many_requests(ROW_LIMIT_REACHED_MESSAGE)
            call_log_row.status = APICallStatusChoices.SUCCESS.value
            call_log_row.save()

            # Update the dataset name if it changed
            new_dataset_name = validated_data["dataset"]["name"]
            if new_dataset_name != dataset.name:
                if Dataset.objects.filter(
                    name=new_dataset_name,
                    deleted=False,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                ).exists():
                    return self._gm.bad_request(
                        get_error_message("DATASET_EXIST_IN_ORG")
                    )
                dataset.name = new_dataset_name
                dataset.save()

            # Set running status for all columns
            Column.objects.filter(dataset=dataset, deleted=False).update(
                status=StatusType.RUNNING.value
            )

            # Handle column changes
            existing_columns = Column.objects.filter(dataset=dataset, deleted=False)
            existing_column_names = {col.name for col in existing_columns}
            new_columns_spec = validated_data["columns"]
            new_column_names = {col["name"] for col in new_columns_spec}

            columns_to_add_spec = [
                col
                for col in new_columns_spec
                if col["name"] not in existing_column_names
            ]
            columns_to_remove = [
                col for col in existing_columns if col.name not in new_column_names
            ]

            for column in columns_to_remove:
                column.deleted = True
                column.save()
                Cell.objects.filter(column=column, dataset=dataset).update(deleted=True)

            newly_created_columns = []
            new_column_ids = []
            if columns_to_add_spec:
                existing_rows_for_new_cols = Row.objects.filter(
                    dataset=dataset, deleted=False
                )
                cells_to_create = []
                for col_spec in columns_to_add_spec:
                    new_column = Column.objects.create(
                        id=uuid.uuid4(),
                        name=col_spec["name"],
                        data_type=determine_data_type_syn_data(col_spec["data_type"]),
                        source=SourceChoices.OTHERS.value,
                        dataset=dataset,
                        status=StatusType.RUNNING.value,
                    )
                    newly_created_columns.append(new_column)
                    new_column_ids.append(str(new_column.id))
                    for row in existing_rows_for_new_cols:
                        cells_to_create.append(
                            Cell(
                                id=uuid.uuid4(),
                                dataset=dataset,
                                column=new_column,
                                row=row,
                                value=None,
                                status=CellStatus.RUNNING.value,
                            )
                        )
                Cell.objects.bulk_create(cells_to_create)

                # Generate data for new columns in existing rows
                gen_new_cols_data = [
                    c for c in columns_to_add_spec if not c.get("skip", False)
                ]
                if gen_new_cols_data and existing_rows_for_new_cols.exists():
                    row_ids = list(
                        existing_rows_for_new_cols.values_list("id", flat=True)
                    )
                    payload_validated_data = {
                        "dataset": {
                            "description": validated_data["dataset"].get(
                                "description", ""
                            ),
                            "objective": validated_data["dataset"].get("objective", ""),
                            "patterns": validated_data["dataset"].get("patterns", ""),
                        }
                    }
                    generate_new_columns.delay(
                        dataset_id=str(dataset.id),
                        row_ids=row_ids,
                        validated_data=payload_validated_data,
                        new_columns_required_info=gen_new_cols_data,
                        new_column_db_model_ids=[c.id for c in newly_created_columns],
                        gen_columns=[],
                        max_order=len(row_ids),
                    )

            # Handle row changes
            existing_rows = Row.objects.filter(dataset=dataset, deleted=False)
            current_row_count = existing_rows.count()
            new_row_count = validated_data["num_rows"]
            new_rows_id = []

            if new_row_count > current_row_count:
                rows_to_add_count = new_row_count - current_row_count
                last_row = existing_rows.order_by("-order").first()
                max_order = last_row.order if last_row else -1

                new_rows = [
                    Row(id=uuid.uuid4(), dataset=dataset, order=max_order + 1 + i)
                    for i in range(rows_to_add_count)
                ]
                new_rows_id = [str(r.id) for r in new_rows]
                Row.objects.bulk_create(new_rows)

                all_columns = Column.objects.filter(dataset=dataset, deleted=False)
                cells_to_create = []
                for row_id in new_rows_id:
                    for col in all_columns:
                        cells_to_create.append(
                            Cell(
                                id=uuid.uuid4(),
                                dataset=dataset,
                                column=col,
                                row_id=row_id,
                                value=None,
                                status=CellStatus.RUNNING.value,
                            )
                        )
                Cell.objects.bulk_create(cells_to_create)

                # Generate data for new rows
                gen_cols_data = [
                    c for c in new_columns_spec if not c.get("skip", False)
                ]
                payload_validated_data = {
                    "dataset": {
                        "description": validated_data["dataset"].get("description", ""),
                        "objective": validated_data["dataset"].get("objective", ""),
                        "patterns": validated_data["dataset"].get("patterns", ""),
                    },
                    "num_rows": len(new_rows_id),
                }
                generate_new_rows.delay(
                    dataset_id=str(dataset.id),
                    validated_data=payload_validated_data,
                    gen_columns=gen_cols_data,
                    new_rows_id=new_rows_id,
                )

            elif new_row_count < current_row_count:
                rows_to_remove_count = current_row_count - new_row_count
                rows_to_remove = existing_rows.order_by("-order")[:rows_to_remove_count]
                row_ids_to_remove = [r.id for r in rows_to_remove]
                Row.objects.filter(id__in=row_ids_to_remove).update(deleted=True)
                Cell.objects.filter(
                    row_id__in=row_ids_to_remove, dataset=dataset
                ).update(deleted=True)

            # Update column order and config
            current_column_order = dataset.column_order or []
            current_column_config = dataset.column_config or {}

            updated_column_order = [
                col_id
                for col_id in current_column_order
                if Column.objects.filter(id=col_id, deleted=False).exists()
            ]
            for col_id in new_column_ids:
                if col_id not in updated_column_order:
                    updated_column_order.append(col_id)
                if col_id not in current_column_config:
                    current_column_config[col_id] = {
                        "is_visible": True,
                        "is_frozen": None,
                    }

            dataset.column_order = updated_column_order
            dataset.column_config = {
                col_id: config
                for col_id, config in current_column_config.items()
                if Column.objects.filter(id=col_id, deleted=False).exists()
            }

            config_data = validated_data.copy()
            if config_data.get("kb_id"):
                config_data["kb_id"] = str(config_data["kb_id"])
            dataset.synthetic_dataset_config = config_data
            dataset.save()

            return self._gm.success_response(
                {
                    "message": "Synthetic dataset configuration updated successfully. Data generation started.",
                    "data": {
                        "dataset_id": str(dataset.id),
                        "dataset_name": dataset.name,
                        "num_rows": validated_data["num_rows"],
                        "num_columns": len(validated_data["columns"]),
                    },
                }
            )

        except Exception as e:
            logger.exception(f"Error in updating synthetic dataset config: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_UPDATE_SYNTHETIC_DATASET_CONFIG")
            )
