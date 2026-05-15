import json
import traceback
import uuid

import structlog
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

logger = structlog.get_logger(__name__)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentDatasetTable
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from futureagi._ee_stubs import APICallStatusChoices, APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import ROW_LIMIT_REACHED_MESSAGE, log_and_deduct_cost_for_resource_request
except ImportError:
    from futureagi._ee_stubs import ROW_LIMIT_REACHED_MESSAGE, log_and_deduct_cost_for_resource_request


class AddRowsFromExistingView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, dataset_id, *args, **kwargs):
        try:
            source_dataset_id = request.data.get("source_dataset_id")
            column_mapping = request.data.get("column_mapping")

            if source_dataset_id == dataset_id:
                return self._gm.bad_request(
                    get_error_message("SOURCE_TARGET_DATASET_ARE_SAME")
                )

            if not source_dataset_id or not column_mapping:
                return self._gm.bad_request(
                    get_error_message("MISSING_SOURCE_DATASET_ID_AND_COLUMN_MAPPINGS")
                )

            # Validate at least one mapping is provided
            if len(column_mapping) == 0:
                return self._gm.bad_request("At least one column mapping is required")

            # Validate no duplicate target columns in mapping
            target_ids = list(column_mapping.values())
            if len(target_ids) != len(set(target_ids)):
                return self._gm.bad_request("Duplicate target columns in mapping")

            # Get both datasets and verify they exist
            target_dataset = get_object_or_404(Dataset, id=dataset_id, deleted=False)
            source_dataset = None

            try:
                source_dataset = Dataset.objects.get(
                    id=source_dataset_id, deleted=False
                )
            except Dataset.DoesNotExist:
                try:

                    experiment_table = ExperimentDatasetTable.objects.select_related(
                        "experiment__dataset"
                    ).get(id=source_dataset_id, deleted=False)

                    experiment = experiment_table.experiment
                    source_dataset = experiment.dataset if experiment else None

                    if not source_dataset:
                        return self._gm.bad_request(
                            "Source experiment dataset not found or has no associated dataset"
                        )

                except ExperimentDatasetTable.DoesNotExist:
                    return self._gm.bad_request("Source dataset not found")

            # --- Row Limit Check Start ---

            existing_rows_count = Row.objects.filter(
                dataset=target_dataset, deleted=False
            ).count()
            new_rows_count = Row.objects.filter(
                dataset=source_dataset, deleted=False
            ).count()
            call_log_row = log_and_deduct_cost_for_resource_request(
                getattr(request, "organization", None) or request.user.organization,
                api_call_type=APICallTypeChoices.ROW_ADD.value,
                config={"total_rows": new_rows_count + existing_rows_count},
                workspace=request.workspace,
            )
            if (
                call_log_row is None
                or call_log_row.status == APICallStatusChoices.RESOURCE_LIMIT.value
            ):
                return self._gm.too_many_requests(ROW_LIMIT_REACHED_MESSAGE)
            call_log_row.status = APICallStatusChoices.SUCCESS.value
            call_log_row.save()
            # --- Row Limit Check End ---

            missing_columns = Column.objects.filter(
                dataset=target_dataset, deleted=False
            ).exclude(id__in=column_mapping.values())

            # Get max order of target dataset to append rows at the end
            last_row = (
                Row.all_objects.filter(dataset=target_dataset)
                .order_by("-created_at")
                .first()
            )
            if last_row:
                max_order = last_row.order
            else:
                max_order = -1

            # Copy rows and cells in batches
            source_rows = Row.objects.filter(
                dataset=source_dataset, deleted=False
            ).order_by("order")
            batch_size = 1000
            current_order = max_order + 1

            for i in range(0, source_rows.count(), batch_size):
                batch_rows = source_rows[i : i + batch_size]
                new_rows = []
                new_cells = []
                row_id_mapping = {}

                # Create new rows
                for row in batch_rows:
                    new_row_id = uuid.uuid4()
                    row_id_mapping[row.id] = new_row_id
                    new_rows.append(
                        Row(
                            id=new_row_id,
                            dataset=target_dataset,
                            order=current_order,
                        )
                    )
                    current_order += 1

                # Bulk create rows
                Row.objects.bulk_create(new_rows)

                # Get cells for current batch of rows
                batch_cells = Cell.objects.filter(
                    row__in=batch_rows, deleted=False
                ).select_related("column")

                # Create new cells
                for cell in batch_cells:
                    if str(cell.column.id) in column_mapping:
                        new_cells.append(
                            Cell(
                                id=uuid.uuid4(),
                                dataset=target_dataset,
                                column_id=column_mapping[str(cell.column.id)],
                                row_id=row_id_mapping[cell.row.id],
                                value=cell.value,
                                value_infos=(
                                    cell.value_infos
                                    if cell.value_infos
                                    else json.dumps({})
                                ),
                                status=cell.status,
                            )
                        )

                for missing_column in missing_columns:
                    for new_row in new_rows:
                        new_cells.append(
                            Cell(
                                id=uuid.uuid4(),
                                dataset=target_dataset,
                                column_id=missing_column.id,
                                row_id=new_row.id,
                                value="",
                            )
                        )

                # Bulk create cells
                if new_cells:
                    Cell.objects.bulk_create(new_cells)

            return self._gm.success_response(
                {
                    "message": "Rows Imported successfully",
                    "rows_added": source_rows.count(),
                }
            )

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in importing rows from existing dataset: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_IMPORT_ROWS_IN_EXISTING_DATASET")
            )
