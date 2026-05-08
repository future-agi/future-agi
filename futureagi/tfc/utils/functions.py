import json
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import numpy as np
import structlog
from django.db.models import Count, F, Sum

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from model_hub.models.choices import CellStatus, SourceChoices
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.utils.SQL_queries import SQLQueryHandler
from tfc.utils.clickhouse import ClickHouseClientSingleton
try:
    from ee.usage.models.usage import APICallStatusChoices
except ImportError:
    from tfc.oss_stubs.usage import APICallStatusChoices


def add_one_day_in_date(date_str):
    # Convert the string to a datetime object
    date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

    # Add one day
    new_date_obj = date_obj + timedelta(days=1)

    # Convert the datetime object back to a string
    new_date_str = new_date_obj.strftime("%Y-%m-%d %H:%M:%S")

    return new_date_str


def sql_query_to_get_count(query):
    clickhouse_client = ClickHouseClientSingleton()
    raw_data_points = clickhouse_client.execute(query)
    total_count = int(raw_data_points[0][0])

    return total_count


def calculate_percentiles(values):
    if not values:
        return {}

    percentiles = {}
    for p in range(5, 101, 5):
        try:
            percentile_value = np.percentile(values, p)
            percentiles[f"p{p}"] = round(percentile_value, 2)
        except Exception:
            continue
    return percentiles


def _extract_numeric_choices(
    cell_value,
    numeric_choice_set,
    *,
    cell_id=None,
    numeric_choices=None,
    log_invalid=False,
):
    import json_repair

    if not cell_value:
        return []

    if isinstance(cell_value, str):
        try:
            selected_choices = json_repair.loads(cell_value)
        except Exception:
            selected_choices = [cell_value]
    else:
        selected_choices = cell_value if isinstance(cell_value, list) else [cell_value]

    numeric_values = []
    for choice in selected_choices:
        try:
            numeric_val = float(choice)
            if numeric_val in numeric_choice_set:
                numeric_values.append(numeric_val)
            elif log_invalid and numeric_choices is not None:
                logger.warning(
                    f"Invalid choice value {numeric_val} not in {numeric_choices} "
                    f"for cell {cell_id}"
                )
        except (ValueError, TypeError):
            if log_invalid:
                logger.warning(f"Non-numeric choice '{choice}' in cell {cell_id}")
            continue

    return numeric_values


def _calculate_numeric_choices_average(cells, multi_choice, numeric_choices):
    """
    TEMPORARY FIX: Calculate numeric average for choices-type evaluations.

    This function is designed to be easily removable when a proper solution is implemented.

    Args:
        cells: Queryset of Cell objects with status='pass'
        multi_choice: Boolean indicating if multiple choices allowed
        numeric_choices: List of valid numeric choice values

    Returns:
        dict: Statistics including average, valid_rows, success_rate, percentiles
    """
    try:
        total_rows = cells.count()

        if total_rows == 0:
            return {
                "average": None,
                "valid_rows": 0,
                "success_rate": 0,
                "percentiles": {},
                "is_numeric_eval_percentage": False,
            }

        numeric_values = []  # Will store the numeric value for each cell
        numeric_choice_set = set(numeric_choices)  # For O(1) validation lookup

        # TEMPORARY FIX: Detect if choices are in 0-1 range for percentage interpretation
        is_percentage_range = len(numeric_choices) > 0 and all(
            0 <= choice <= 1 for choice in numeric_choices
        )

        for cell in cells:
            try:
                cell_numeric_values = _extract_numeric_choices(
                    cell.value,
                    numeric_choice_set,
                    cell_id=cell.id,
                    numeric_choices=numeric_choices,
                    log_invalid=True,
                )

                # Calculate average for this cell (for multi-choice)
                if cell_numeric_values:
                    if multi_choice:
                        # For multi-choice, average the selected values for this cell
                        cell_avg = sum(cell_numeric_values) / len(cell_numeric_values)
                    else:
                        # For single choice, just use the value
                        cell_avg = cell_numeric_values[0]
                    numeric_values.append(cell_avg)

            except Exception as e:
                logger.warning(f"Error processing cell {cell.id}: {str(e)}")
                continue

        # Calculate overall statistics
        if not numeric_values:
            return {
                "average": None,
                "valid_rows": 0,
                "success_rate": 0,
                "percentiles": {},
                "is_numeric_eval_percentage": False,
            }

        # Calculate average
        average = round(sum(numeric_values) / len(numeric_values), 2)

        # TEMPORARY FIX: Convert to percentage if in 0-1 range
        if is_percentage_range:
            average = round(average * 100, 2)

        valid_rows = len(numeric_values)
        success_rate = round((valid_rows / total_rows) * 100, 2)

        # Calculate percentiles using existing function
        percentiles = calculate_percentiles(numeric_values)

        return {
            "average": average,
            "valid_rows": valid_rows,
            "success_rate": success_rate,
            "percentiles": percentiles,
            "is_numeric_eval_percentage": is_percentage_range,
        }

    except Exception as e:
        logger.error(f"Error calculating numeric choices average: {str(e)}")
        traceback.print_exc()
        return {
            "average": None,
            "valid_rows": 0,
            "success_rate": 0,
            "percentiles": {},
            "is_numeric_eval_percentage": False,
            "error": str(e),
        }


def _is_template_numeric_choices(eval_template):
    """
    TEMPORARY FIX: Check if eval template has all numeric choices and is user-created.

    This function is designed to be easily removable when a proper solution is implemented.

    Args:
        eval_template: EvalTemplate instance

    Returns:
        tuple: (is_numeric: bool, numeric_choices: list[float] | None)
               Returns (True, [list of floats]) if all choices are numeric
               Returns (False, None) otherwise
    """
    try:
        from model_hub.models.choices import OwnerChoices

        # Must be user-created
        if eval_template.owner != OwnerChoices.USER.value:
            return False, None

        # Must have choices
        choices = eval_template.choices
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            return False, None

        # All choices must be numeric (int, float, or string representation)
        numeric_choices = []
        for choice in choices:
            try:
                numeric_value = float(choice)
                numeric_choices.append(numeric_value)
            except (ValueError, TypeError):
                # If any choice is not numeric, return False
                return False, None

        return True, numeric_choices

    except Exception as e:
        logger.error(f"Error checking template numeric choices: {str(e)}")
        return False, None


def get_numeric_choices_score_format(
    eval_template, dataset_id, user_eval_metric_ids=None, row_ids=None
):
    """
    TEMPORARY FIX: Calculate numeric choice stats in the same format as get_score_stats.

    This function is designed to be easily removable when a proper solution is implemented.

    Returns statistics in the exact same format as get_score_stats so the frontend
    renders tables instead of pie charts. Statistics shown: mean, median, std_dev, q3, max.

    Args:
        eval_template: EvalTemplate instance
        dataset_id: Dataset ID
        user_eval_metric_ids: Optional list of specific metric IDs
        row_ids: Optional list of specific row IDs

    Returns:
        dict: {"result": [...], "total_avg": float} in same format as get_score_stats
        None: If calculation fails
    """
    try:
        from model_hub.models.choices import CellStatus, SourceChoices
        from model_hub.models.develop_dataset import Cell, Column
        from model_hub.models.evals_metric import UserEvalMetric

        # Validate and get numeric choices using helper
        is_numeric, numeric_choices = _is_template_numeric_choices(eval_template)
        if not is_numeric:
            return None

        numeric_choice_set = set(numeric_choices)

        # TEMPORARY FIX: Detect if choices are in 0-1 range for percentage interpretation
        is_percentage_range = len(numeric_choices) > 0 and all(
            0 <= choice <= 1 for choice in numeric_choices
        )

        # Get all UserEvalMetric instances for this template
        metric_filter = {
            "template": eval_template,
            "dataset_id": dataset_id,
            "deleted": False,
        }
        if user_eval_metric_ids:
            metric_filter["id__in"] = user_eval_metric_ids

        metrics = list(UserEvalMetric.objects.filter(**metric_filter))

        if not metrics:
            return {"result": [], "total_avg": None}

        metric_ids = [str(metric.id) for metric in metrics]

        columns = Column.objects.filter(
            source=SourceChoices.EVALUATION.value,
            source_id__in=metric_ids,
            dataset_id=dataset_id,
            deleted=False,
        )
        columns_by_source_id = {column.source_id: column for column in columns}
        column_ids = [column.id for column in columns]

        cells_query = Cell.objects.filter(
            column_id__in=column_ids,
            status=CellStatus.PASS.value,
            deleted=False,
            row__deleted=False,
        )
        if row_ids:
            cells_query = cells_query.filter(row_id__in=row_ids)

        cells_by_column_id = {}
        for cell in cells_query:
            cells_by_column_id.setdefault(cell.column_id, []).append(cell)

        result = []
        all_numeric_values = []  # For overall average

        for metric in metrics:
            column = columns_by_source_id.get(str(metric.id))
            if not column:
                continue

            cells = cells_by_column_id.get(column.id, [])
            total_cells = len(cells)

            if total_cells == 0:
                continue

            # Extract numeric values from cells
            metric_numeric_values = []
            multi_choice = metric.template.multi_choice or False

            for cell in cells:
                try:
                    if not cell.value:
                        continue

                    cell_numeric_values = _extract_numeric_choices(
                        cell.value,
                        numeric_choice_set,
                        cell_id=cell.id,
                    )

                    # Calculate cell average
                    if cell_numeric_values:
                        if multi_choice:
                            cell_avg = sum(cell_numeric_values) / len(
                                cell_numeric_values
                            )
                        else:
                            cell_avg = cell_numeric_values[0]
                        metric_numeric_values.append(cell_avg)
                        all_numeric_values.append(cell_avg)

                except Exception as e:
                    logger.warning(f"Error processing cell {cell.id}: {str(e)}")
                    continue

            # Calculate distribution: count occurrences of each numeric choice
            if metric_numeric_values:
                # Initialize distribution dict with all possible choices set to 0
                distribution = {
                    str(int(choice) if choice.is_integer() else choice): 0
                    for choice in numeric_choices
                }

                # Count occurrences
                for value in metric_numeric_values:
                    # Convert to string key (use int format if it's a whole number)
                    key = str(int(value) if value == int(value) else value)
                    if key in distribution:
                        distribution[key] += 1

                # Convert counts to percentages
                total_count = len(metric_numeric_values)
                distribution_percentages = {
                    key: round((count / total_count), 2)
                    for key, count in distribution.items()
                }

                # Format response (output keys are numeric choices, values are percentages)
                response = {
                    "name": metric.name,
                    "id": str(metric.id),
                    "total_cells": total_cells,
                    "output": distribution,
                }
                result.append(response)

        # Calculate overall average
        total_avg = None
        if all_numeric_values:
            overall_avg = sum(all_numeric_values) / len(all_numeric_values)
            total_avg = round(overall_avg, 2)

            # TEMPORARY FIX: Convert to percentage if in 0-1 range
            if is_percentage_range:
                total_avg = round(total_avg * 100, 2)

        return {
            "result": result,
            "total_avg": total_avg,
            "is_numeric_eval_percentage": is_percentage_range,
        }

    except Exception as e:
        logger.error(f"Error calculating numeric choices score format: {str(e)}")
        traceback.print_exc()
        return None


def calculate_column_average(column_id, row_ids=None):
    """
    Calculate average for a column based on its data type and output format.

    Args:
        column_id: The ID of the column to analyze
    Returns:
        dict: A dictionary containing average and other relevant statistics
    """
    try:
        if isinstance(column_id, Column):
            column = column_id
        else:
            column = Column.objects.prefetch_related("cell_set").get(id=column_id)

        stats = {
            "total_rows": 0,
            "valid_rows": 0,
            "success_rate": 0,
            "average": None,
        }

        # For evaluation columns, check the output type
        if column.source in [
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
            SourceChoices.EVALUATION_TAGS.value,
            SourceChoices.OPTIMISATION_EVALUATION_TAGS.value,
            SourceChoices.EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.OPTIMISATION_EVALUATION.value,
        ]:
            if row_ids:
                cells = column.cell_set.filter(
                    row__deleted=False,
                    status=CellStatus.PASS.value,
                    row_id__in=row_ids,
                    deleted=False,
                )
            else:
                cells = column.cell_set.filter(
                    row__deleted=False, status=CellStatus.PASS.value, deleted=False
                )

            if not cells.exists():
                logger.warning(f"No valid cells found for column ID {column_id}")
                return {
                    "average": None,
                    "total_rows": 0,
                    "valid_rows": 0,
                    "success_rate": 0,
                }

            total_rows = cells.count()

            stats = {
                "total_rows": total_rows,
                "valid_rows": 0,
                "success_rate": 0,
                "average": None,
            }

            if not column.name.endswith("-reason"):
                try:
                    # Get the evaluation template's output type
                    if column.source == SourceChoices.EVALUATION.value:
                        eval_metric = UserEvalMetric.objects.get(id=column.source_id)
                    else:
                        # Extract original eval metric ID from composite source_id
                        original_metric_id = column.source_id.split("-sourceid-")[1]
                        eval_metric = UserEvalMetric.objects.get(id=original_metric_id)

                    output_type = (
                        eval_metric.template.config.get("output")
                        if "_tags" not in column.source
                        else "choices"
                    )

                    if output_type == "Pass/Fail":
                        # Calculate percentage of passes
                        passed_count = cells.filter(value="Passed").count()
                        stats["average"] = round((passed_count / total_rows) * 100, 2)
                        stats["valid_rows"] = total_rows
                        stats["success_rate"] = stats["average"]

                        # Calculate percentiles for pass rates
                        all_cells = list(
                            cells.order_by("id").values_list("value", flat=True)
                        )
                        pass_values = [
                            1 if value == "Passed" else 0 for value in all_cells
                        ]
                        percentiles = calculate_percentiles(pass_values)
                        stats["percentiles"] = percentiles

                    elif output_type in ["score", "numeric"]:
                        # Handle score and numeric type evaluations
                        valid_scores = []
                        for cell in cells:
                            try:
                                if cell.value and cell.status == "pass":
                                    if isinstance(cell.value, dict):
                                        scores = cell.value
                                    else:
                                        try:
                                            scores = json.loads(
                                                cell.value.replace("'", '"')
                                            )
                                            if isinstance(
                                                scores, (float, int)
                                            ):  # noqa: UP038
                                                scores = {"single_score": scores}
                                        except json.JSONDecodeError:
                                            if cell.value in ["Passed", "Failed"]:
                                                scores = {
                                                    "single_score": (
                                                        1
                                                        if cell.value == "Passed"
                                                        else 0
                                                    )
                                                }
                                            else:
                                                scores = {
                                                    "single_score": float(cell.value)
                                                }

                                    if isinstance(scores, dict):
                                        cell_scores = [
                                            float(score)
                                            for score in scores.values()
                                            if str(score).replace(".", "").isdigit()
                                        ]
                                        if cell_scores:
                                            valid_scores.append(
                                                sum(cell_scores) / len(cell_scores)
                                            )
                            except Exception as e:
                                logger.error(f"Error processing cell: {str(e)}")
                                traceback.print_exc()
                                continue
                        if valid_scores:
                            stats["average"] = round(
                                (sum(valid_scores) / total_rows) * 100, 2
                            )
                            stats["valid_rows"] = len(valid_scores)
                            stats["success_rate"] = round(
                                (len(valid_scores) / total_rows) * 100, 2
                            )

                            percentiles = calculate_percentiles(valid_scores)
                            stats["percentiles"] = percentiles

                    elif output_type in ["reason", "choices"]:
                        # TEMPORARY FIX: Check if this is a user-created numeric choices eval
                        # If yes, calculate actual numeric average instead of just counting valid responses
                        numeric_eval_handled = False

                        if (
                            output_type == "choices"
                        ):  # Only apply to choices, not reason
                            is_numeric, numeric_choices = _is_template_numeric_choices(
                                eval_metric.template
                            )
                            if is_numeric:
                                # Calculate numeric average for user-created numeric evaluations
                                multi_choice = (
                                    eval_metric.template.multi_choice or False
                                )
                                numeric_stats = _calculate_numeric_choices_average(
                                    cells, multi_choice, numeric_choices
                                )
                                stats.update(numeric_stats)
                                # Update stats with numeric_stats values, but also update "is_numeric_eval" to True
                                stats["is_numeric_eval"] = True
                                numeric_eval_handled = True

                        # EXISTING CATEGORICAL LOGIC (for non-numeric or system evals)
                        if not numeric_eval_handled:
                            valid_responses = cells.filter(status="pass").count()
                            stats["valid_rows"] = valid_responses
                            stats["average"] = round(
                                (valid_responses / total_rows) * 100, 2
                            )

                            # Calculate percentiles based on pass/fail status
                            all_cells = list(
                                cells.order_by("id").values_list("status", flat=True)
                            )
                            pass_values = [
                                1 if value == "pass" else 0 for value in all_cells
                            ]
                            stats["percentiles"] = calculate_percentiles(pass_values)

                            stats["success_rate"] = stats["average"]

                except UserEvalMetric.DoesNotExist:
                    logger.error(
                        f"UserEvalMetric does not exist for column {column_id}"
                    )
                    stats["average"] = None

            # Calculate success rate if not already set
            if "success_rate" not in stats:
                stats["success_rate"] = round(
                    (stats["valid_rows"] / total_rows) * 100, 2
                )

        return stats

    except Column.DoesNotExist:
        logger.error(f"Column with ID {column_id} does not exist")
        return {
            "average": None,
            "total_rows": 0,
            "valid_rows": 0,
            "percentiles": {},
            "success_rate": 0,
        }
    except Exception as e:
        traceback.print_exc()
        return {
            "average": None,
            "total_rows": 0,
            "valid_rows": 0,
            "percentiles": {},
            "success_rate": 0,
            "error": str(e),
        }


def calculate_eval_average(eval_template, api_logs):
    """
    Calculate average and related metrics for evaluations based on the eval template and logs.
    Args:
        eval_template: The evaluation template object.
        api_logs: Queryset/List of APICallLog objects
    Returns:
        float: Average score or 0 if no valid logs.
    """
    try:
        output_type = eval_template.config.get("output")
        success_count = 0
        valid_logs = 0

        choices_map = eval_template.config.get("choices_map")

        # Process all logs in memory to avoid additional queries
        for log in api_logs:
            try:
                if log["status"] != APICallStatusChoices.SUCCESS.value:
                    continue
                output_json = json.loads(log["config"])
                output = output_json.get("output")

                if output_type == "Pass/Fail":
                    if output.get("output") == "Passed":
                        success_count += 1
                    valid_logs += 1

                elif output_type == "score":
                    try:
                        score = round(float(output.get("output")), 2)
                        success_count += score
                        valid_logs += 1
                    except (ValueError, TypeError):
                        continue

                elif output_type in ["choices", "reason"]:
                    if choices_map and not eval_template.multi_choice:
                        match choices_map.get(output.get("output")[0]):
                            case "pass":
                                success_count += 1
                                valid_logs += 1
                            case "neutral":
                                success_count += 0.5
                                valid_logs += 1
                            case _:
                                valid_logs += 1
                    else:
                        success_count += 1
                        valid_logs += 1

            except Exception:
                continue

        if output_type == "Pass/Fail":
            average = round((success_count / valid_logs) * 100, 2) if valid_logs else 0
        elif output_type == "score":
            average = round((success_count / valid_logs) * 100, 2) if valid_logs else 0
        elif output_type in ["choices", "reason"]:
            average = round((success_count / valid_logs) * 100, 2) if valid_logs else 0

        return average

    except Exception as e:
        logger.error(f"Error calculating eval average: {str(e)}")
        traceback.print_exc()
        return 0


def get_eval_stats(eval_template, dataset_id, user_eval_metric_ids=None, row_ids=None):
    output_type = eval_template.config.get("output")
    result = None
    if output_type == "Pass/Fail":
        response = get_pass_fail_stats(
            eval_template, dataset_id, user_eval_metric_ids, row_ids
        )
        result = {
            "name": eval_template.name,
            "total_pass_rate": response["total_pass_rate"],
            "result": response["result"],
            "output_type": output_type,
            "id": str(eval_template.id),
        }

    elif output_type == "score":
        response = get_score_stats(
            eval_template, dataset_id, user_eval_metric_ids, row_ids
        )
        result = {
            "name": eval_template.name,
            "total_avg": response["total_avg"],
            "result": response["result"],
            "output_type": output_type,
            "id": str(eval_template.id),
        }

    elif output_type in ["choices", "reason"]:
        # TEMPORARY FIX: Check if this is numeric choices
        # If yes, format as "score" type so frontend renders tables instead of pie charts
        numeric_response = None
        if output_type == "choices":
            is_numeric, _ = _is_template_numeric_choices(eval_template)
            if is_numeric:
                numeric_response = get_numeric_choices_score_format(
                    eval_template, dataset_id, user_eval_metric_ids, row_ids
                )

        if numeric_response is not None:
            # Return in same format as score stats with output_type="score"
            result = {
                "name": eval_template.name,
                "total_avg": numeric_response["total_avg"],
                "result": numeric_response["result"],
                "output_type": "score",  # Pretend it's score so frontend renders correctly
                "id": str(eval_template.id),
                "is_numeric_eval": True,
                "is_numeric_eval_percentage": numeric_response.get(
                    "is_numeric_eval_percentage", False
                ),
            }
        else:
            # Fall back to categorical stats (pie chart)
            response = get_choices_stats(
                eval_template, dataset_id, user_eval_metric_ids, row_ids
            )
            result = {
                "name": eval_template.name,
                "total_choices_avg": response["total_avg"],
                "result": response["result"],
                "output_type": output_type,
                "id": str(eval_template.id),
            }

    return result


def get_pass_fail_stats(
    eval_template, dataset_id, user_eval_metric_ids=None, row_ids=None
):
    sql_result = SQLQueryHandler.get_cells_pass_fail_rates(
        dataset_id=dataset_id,
        eval_template_id=str(eval_template.id),
        user_eval_metric_ids=user_eval_metric_ids,
        row_ids=row_ids,
    )

    result = []
    total_pass_avg = None

    for row in sql_result:
        (
            column_id,
            column_name,
            user_eval_metric_id,
            metric_name,
            total_cells,
            passed_count,
            failed_count,
            pass_rate,
            fail_rate,
            total_cells_overall,
            total_passed_overall,
            total_failed_overall,
            overall_pass_rate,
        ) = row

        total_pass_avg = overall_pass_rate

        response = {
            "name": metric_name,
            "id": user_eval_metric_id,
            "total_cells": total_cells,
            "output": {
                "pass": pass_rate,
                "fail": fail_rate,
                "pass_count": passed_count,
                "fail_count": failed_count,
            },
        }

        result.append(response)

    return {"result": result, "total_pass_rate": total_pass_avg}


def get_score_stats(eval_template, dataset_id, user_eval_metric_ids=None, row_ids=None):
    sql_result = SQLQueryHandler.get_cells_percentile_distribution(
        dataset_id=dataset_id,
        eval_template_id=str(eval_template.id),
        user_eval_metric_ids=user_eval_metric_ids,
        row_ids=row_ids,
    )

    result = []
    total_avg = None

    for row in sql_result:
        (
            column_id,
            column_name,
            user_eval_metric_id,
            metric_name,
            total_cells,
            column_avg,
            p5,
            p10,
            p20,
            p30,
            p40,
            p50,
            p60,
            p70,
            p80,
            p90,
            p95,
            p100,
            min_value,
            max_value,
            total_cells_overall,
            overall_avg,
            overall_min,
            overall_max,
        ) = row

        total_avg = overall_avg * 100

        response = {
            "name": metric_name,
            "id": user_eval_metric_id,
            "total_cells": total_cells,
            "output": {
                "p5": p5,
                "p10": p10,
                "p20": p20,
                "p30": p30,
                "p40": p40,
                "p50": p50,
                "p60": p60,
                "p70": p70,
                "p80": p80,
                "p90": p90,
                "p95": p95,
                "p100": p100,
            },
        }

        result.append(response)

    return {"result": result, "total_avg": total_avg}


def get_choices_stats(
    eval_template, dataset_id, user_eval_metric_ids=None, row_ids=None
):
    sql_result = SQLQueryHandler.get_cells_choices_analysis(
        dataset_id=dataset_id,
        eval_template_id=str(eval_template.id),
        choices=eval_template.choices,
        user_eval_metric_ids=user_eval_metric_ids,
        row_ids=row_ids,
    )

    result = []
    total_avg = {}
    result_metric_map = {}

    for row in sql_result:
        (
            column_id,
            column_name,
            user_eval_metric_id,
            metric_name,
            choice,
            total_cells,
            choice_count,
            choice_percentage,
            total_cells_overall,
            total_choice_count_overall,
            overall_choice_percentage,
        ) = row

        if metric_name not in result_metric_map:
            result_metric_map[metric_name] = {}

        result_metric_map[metric_name][choice] = choice_percentage

        if choice not in total_avg:
            total_avg[choice] = overall_choice_percentage

    for metric_name in result_metric_map.keys():
        response = {
            "name": metric_name,
            "total_cells": total_cells,
            "output": result_metric_map[metric_name],
        }
        result.append(response)

    return {"result": result, "total_avg": total_avg}


def get_prompt_stats(run_prompters, dataset_id, row_ids=None):
    result = []
    total_tokens = 0
    total_cost = 0
    total_time = 0
    total_cells = 0

    def process_run_prompter(run_prompter):
        filter_kwargs = {}
        if row_ids:
            filter_kwargs["row_id__in"] = row_ids

        cells_stats = Cell.objects.filter(
            column__source=SourceChoices.RUN_PROMPT.value,
            column__source_id=str(run_prompter.id),
            dataset_id=dataset_id,
            deleted=False,
            **filter_kwargs,
        ).aggregate(
            total_prompt_tokens=Sum(F("prompt_tokens"), default=0),
            total_completion_tokens=Sum(F("completion_tokens"), default=0),
            total_response_time=Sum(F("response_time"), default=0),
            cells_count=Count("id"),
        )

        cells_count = cells_stats.get("cells_count", 0)
        total_response_time = cells_stats.get("total_response_time", 0)
        total_prompt_tokens = cells_stats.get("total_prompt_tokens", 0)
        total_completion_tokens = cells_stats.get("total_completion_tokens", 0)

        prompt_result = {
            "id": str(run_prompter.id),
            "name": run_prompter.name,
            "input_token": (
                round(total_prompt_tokens / cells_count, 2) if cells_count > 0 else 0
            ),
            "output_token": (
                round(total_completion_tokens / cells_count, 2)
                if cells_count > 0
                else 0
            ),
            "total_token": (
                round((total_prompt_tokens + total_completion_tokens) / cells_count, 2)
                if cells_count > 0
                else 0
            ),
        }

        calculated_cost = calculate_total_cost(
            run_prompter.model,
            {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
            },
        )

        return {
            "prompt_result": prompt_result,
            "calculated_cost": (
                calculated_cost.get("total_cost", 0) if calculated_cost else 0
            ),
            "total_response_time": total_response_time,
            "cells_count": cells_count,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
        }

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_prompter = {
            executor.submit(process_run_prompter, rp): rp for rp in run_prompters
        }
        for future in as_completed(future_to_prompter):
            res = future.result()
            result.append(res["prompt_result"])
            total_cost += res["calculated_cost"]
            total_time += res["total_response_time"]
            total_cells += res["cells_count"]
            total_tokens += res["total_tokens"]

    response = {
        "avg_tokens": round(total_tokens / total_cells, 2) if total_cells > 0 else 0,
        "avg_cost": round(total_cost / total_cells, 4) if total_cells > 0 else 0,
        "avg_time": round(total_time / total_cells, 2) if total_cells > 0 else 0,
        "prompts": result,
    }

    return response
