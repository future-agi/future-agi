import re

from model_hub.models.metric import Metric
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.utils.constant import journey_operator_map
from tfc.utils.clickhouse import ClickHouseClientSingleton
from tfc.utils.functions import add_one_day_in_date

_WHOLE_NUMBER = re.compile(r"\s*[+-]?\d+\s*")


def event_filters(org_id, model_id, dataset, start_date, end_date, filter_end_date):
    """The model, organisation, dataset and date filters of a Performance read.

    Returns the filter text and the parameters it names. Each value travels as
    a parameter the driver quotes. The environment is the events number the
    shared helper gives for a name or the number "1" to "4", or None, which
    matches no event.
    """
    params = {
        "model_id": str(model_id),
        "org_id": str(org_id),
        "version": str(dataset["version"]),
        "environment": OptimizeDataset.EnvTypes.events_number(dataset["environment"]),
        "start_date": str(start_date),
        "end_date": str(end_date),
        "filter_end_date": str(filter_end_date),
    }
    prep_filters = [
        " AIModel = %(model_id)s ",
        " OrgID = %(org_id)s ",
        " ModelVersion = %(version)s ",
        " Environment = %(environment)s ",
        " EventDateTime BETWEEN %(start_date)s AND %(filter_end_date)s ",
        " deleted = 0 ",
    ]
    return prep_filters, params


def value_list(values):
    """A filter's values as a list of text and numbers for the driver to quote."""
    if not isinstance(values, list) or any(
        isinstance(item, bool) or not isinstance(item, str | int | float)
        for item in values
    ):
        raise ValueError("Filter values must be a list of text or numbers.")
    return values


def number_value(value):
    """A number filter's value as an int or a float.

    The Performance page sends the number as text and an API caller may send a
    JSON number; both read as the same number. Anything else is refused.
    """
    if isinstance(value, str):
        value = int(value) if _WHOLE_NUMBER.fullmatch(value) else float(value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("A number filter value must be a number.")
    return value


def _filter_condition(index, request_filter, metric_id, params):
    """The condition text for one request filter, or "" when it has no value.

    The filter's key, values and metric key join params; the operator maps
    through the fixed operator table.
    """
    filter_type = request_filter["type"]
    values = request_filter["values"]
    operator = journey_operator_map[request_filter["operator"]]
    name = f"filter_{index}"

    if filter_type == "property":
        params[f"{name}_key"] = str(request_filter["key"])
        if request_filter["datatype"] == "string":
            params[f"{name}_values"] = value_list(values)
            return (
                f"arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                IN (SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                AS conv_id FROM events WHERE hasAll(splitByString(';',arrayElement(EvalResults.Value, \
                indexOf(EvalResults.Key, %({name}_key)s))),%({name}_values)s) \
                AND NOT has(Features.Key, 'node_id') AND deleted = 0)"
            )
        params[f"{name}_value"] = number_value(values[0])
        return f"toFloat32(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %({name}_key)s))) {operator} %({name}_value)s"

    if filter_type == "performanceMetric":
        if values[0] == "":
            return ""
        values[0] = str(float(values[0]) / 10)
        params[f"{name}_score_key"] = f"metric_{request_filter['key_id']}_score"
        params[f"{name}_value"] = float(values[0])
        metric_value = f"arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %({name}_score_key)s))"
        return f"if(isNotNull({metric_value}) AND {metric_value} != '', toFloat32({metric_value}), 0) {operator} %({name}_value)s"

    if filter_type == "performanceTag":
        params["metric_tags_key"] = f"metric_{metric_id}_tags"
        params[f"{name}_values"] = value_list(values)
        return f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(metric_tags_key)s))),%({name}_values)s)"

    return ""


def calculate_performance(
    org_id, model_id, dataset, metric, filters, positive_class=None, k=None
):
    start_date = dataset["start_date"]
    end_date = add_one_day_in_date(dataset["end_date"])
    tags = dataset["tags"] if "tags" in dataset else []

    prep_filters, params = event_filters(
        org_id, model_id, dataset, start_date, end_date, end_date
    )
    params["agg_by"] = str(dataset["agg_by"])
    params["metric_score_key"] = f"metric_{metric.id}_score"

    additional_select = []

    if len(tags) > 0:
        params["metric_tags_key"] = f"metric_{metric.id}_tags"
        params["tags"] = value_list(tags)
        prep_filters.append(
            " hasAll(splitByString(';', EvalResults.Value[indexOf(EvalResults.Key, %(metric_tags_key)s)]), %(tags)s) "
        )

    for filter_index, filter in enumerate(filters):
        # filter_type = filter["type"]
        # property = filter["property"]
        comparison = get_comparison_sign(filter["comparison"])
        # dataset_to_filter_environment = filter["dataset"]["environment"]
        filter_vals = filter["values"]
        conversion_function = get_conversion_function(filter["dataType"])
        if comparison is None or conversion_function is None:
            raise ValueError("Unknown filter comparison or data type.")

        feat_name = f"filter_{filter_index}"
        params[f"{feat_name}_version"] = str(filter["dataset"]["version"])

        # db_nested_prop = ""
        # if filter_type == "feature":
        #     db_nested_prop = "Features"

        # if filter_type == "tag":
        #     db_nested_prop = "Tags"

        # if filter_type == "prediction":
        #     db_nested_prop = "PredictionLabel"

        # if filter_type == "actual":
        #     db_nested_prop = "ActualLabel"

        additional_select.append(
            " {conversion_function}(arrayElement({db_nested_prop}.Value, indexOf({db_nested_prop}.Key, '{property}'))) AS {feat_name} "
        )

        query_arr = []

        for value_index, filter_val in enumerate(filter_vals):
            placeholder = f"{feat_name}_value_{value_index}"
            params[placeholder] = str(filter_val)
            query_arr.append(
                f" {feat_name} {comparison} {conversion_function}(%({placeholder})s) "
            )

        query = " OR ".join(query_arr)
        query = f"""(
            ({query}) AND (
                AIModel = %(model_id)s,
                OrgID = %(org_id)s,
                ModelVersion = %({feat_name}_version)s,
                Environment = %(environment)s
            )
        )"""

        prep_filters.append(query)

    if len(additional_select) > 0:
        select_query = " , " + " , ".join(additional_select)
    else:
        select_query = ""

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    metric_type_filter = ""

    if metric.metric_type == Metric.MetricTypes.STEPWISE_MODEL_INFERENCE:
        metric_type_filter = " AND  has(Features.Key, 'node_id') "

    if metric.metric_type == Metric.MetricTypes.WHOLE_USER_OUTPUT:
        metric_type_filter = " AND NOT has(Features.Key, 'node_id') "

    final_query = ""
    # if metric == "accuracy":
    # final_query = f"""
    #     SELECT
    #         agg_time AS time_interval,
    #         COUNT() AS total_events,
    #         AVG(toFloat32(metricValue)) AS correct_predictions
    #     FROM (
    #         SELECT
    #             UUID,
    #             CASE
    #                 WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(EventDateTime)
    #                 WHEN '{agg_by}' = 'daily' THEN toDate(EventDate)
    #                 WHEN '{agg_by}' = 'weekly' THEN toMonday(EventDate)
    #                 WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(EventDate)
    #             END AS agg_time,
    #             arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric.id}_score')) AS metricValue
    #             {select_query}
    #             {metric_type_filter}
    #         FROM events
    #         WHERE EventDateTime BETWEEN '{start_date}' AND '{end_date}'
    #             {filter_query}
    #             {metric_type_filter}
    #         AND deleted=0
    #     )
    #     GROUP BY time_interval
    #     ORDER BY time_interval
    #     """

    # DONT ASK HOW I WROTE THIS :)
    final_query = f"""
        WITH
            toDateTime(%(start_date)s) AS start_date,
            toDateTime(%(end_date)s) AS end_date,
            CASE
                WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(start_date)
                WHEN %(agg_by)s = 'daily' THEN toDate(start_date)
                WHEN %(agg_by)s = 'weekly' THEN toStartOfWeek(toDate(start_date))
                WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(start_date)
            END AS interval_start,
            CASE
                WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(end_date)
                WHEN %(agg_by)s = 'daily' THEN toDate(end_date)
                WHEN %(agg_by)s = 'weekly' THEN toLastDayOfWeek(toDate(end_date))
                WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(end_date)
            END AS interval_end,
            CASE
                WHEN %(agg_by)s = 'hourly' THEN 'hour'
                WHEN %(agg_by)s = 'daily' THEN 'day'
                WHEN %(agg_by)s = 'weekly' THEN 'week'
                WHEN %(agg_by)s = 'monthly' THEN 'month'
            END AS interval_type

        SELECT
            time_series.time_interval,
            event_data.total_events,
            event_data.correct_predictions
        FROM (
            SELECT
                arrayJoin(arrayMap(x -> toDateTime(x),
                    range(toUInt32(interval_start), toUInt32(interval_end),
                        CASE
                            WHEN interval_type = 'hour' THEN 3600
                            WHEN interval_type = 'day' THEN 86400
                            WHEN interval_type = 'week' THEN 604800
                            WHEN interval_type = 'month' THEN 2592000
                        END
                    )
                )) AS time_interval
        ) AS time_series
        LEFT JOIN (
            SELECT
                agg_time AS time_interval,
                COUNT() AS total_events,
                AVG(if(isNotNull(metricValue) AND metricValue != '', toFloat32(metricValue), 0)) AS correct_predictions
            FROM (
                SELECT
                    UUID,
                    CASE
                        WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(EventDateTime)
                        WHEN %(agg_by)s = 'daily' THEN toDate(EventDate)
                        WHEN %(agg_by)s = 'weekly' THEN toStartOfWeek(EventDate)
                        WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(EventDate)
                    END AS agg_time,
                    arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(metric_score_key)s)) AS metricValue
                    {select_query}
                FROM events
                WHERE EventDateTime BETWEEN %(start_date)s AND %(end_date)s
                    {filter_query}
                    {metric_type_filter}
            )
            GROUP BY time_interval
        ) AS event_data ON time_series.time_interval = event_data.time_interval
        ORDER BY time_series.time_interval

    """

    # if metric == "":

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query, params)
    return results


def calculate_performance_details(
    org_id,
    model_id,
    dataset,
    filters,
    start_date,
    end_date,
    offset=None,
    limit=10,
    unpaginated=False,
):
    metric_id = dataset["metric_id"]

    prep_filters, params = event_filters(
        org_id,
        model_id,
        dataset,
        start_date,
        end_date,
        add_one_day_in_date(end_date),
    )
    params["metric_score_key"] = f"metric_{metric_id}_score"
    params["metric_explanation_key"] = f"metric_{metric_id}_explanation"
    params["metric_tags_key"] = f"metric_{metric_id}_tags"

    for filter_index, filter in enumerate(filters):
        query = _filter_condition(filter_index, filter, metric_id, params)

        if query.strip() != "":
            prep_filters.append(query)

    # if len(tags) > 0:
    #     prep_filters.append(
    #         f" hasAll(splitByString(';', EvalResults.Value[indexOf(EvalResults.Key, 'metric_{metric.id}_tags')]), {tags}) "
    #     )

    # additional_select = []

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    limit_query = ""
    offset_query = ""

    if unpaginated is False:
        params["limit"] = limit + 1
        params["offset"] = offset or 0
        limit_query = "LIMIT %(limit)s"
        offset_query = "OFFSET %(offset)s"

    metric_type_filter = " AND  has(Features.Key, 'node_id') "

    # if metric.metric_type == Metric.MetricTypes.STEPWISE_MODEL_INFERENCE:
    #     metric_type_filter = " AND  has(Features.Key, 'node_id') "

    # if metric.metric_type == Metric.MetricTypes.WHOLE_USER_OUTPUT:
    #     metric_type_filter = " AND NOT has(Features.Key, 'node_id') "

    order_query = ""
    # if orderOption == "latest":
    #     order_query = "ORDER BY EventDateTime DESC"
    # elif orderOption == "earliest":
    #     order_query = "ORDER BY EventDateTime ASC"
    # elif orderOption == "lowestScore":
    #     order_query = "ORDER BY metric_score ASC"
    # elif orderOption == "highestScore":
    #     order_query = "ORDER BY metric_score DESC"
    # else:
    #     order_query = "ORDER BY EventDateTime DESC"
    #     limit_query = "LIMIT 10000"

    final_query = ""
    # if metric == "accuracy":
    final_query = f"""
            WITH
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(metric_score_key)s)) AS raw_metric_score
            SELECT
                UUID,
                EventDateTime,
                if(isNotNull(raw_metric_score) AND raw_metric_score != '', toFloat32(raw_metric_score), 0) AS metric_score,
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(metric_explanation_key)s)) AS metric_explanation,
                arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS input_node_id,
                arrayElement(PredictionLabel.Value, indexOf(PredictionLabel.Key, 'node_id')) AS output_node_id,
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(metric_tags_key)s)) AS metric_tags,
                arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) AS conversation_id
            FROM events
            WHERE EventDateTime BETWEEN %(start_date)s AND %(end_date)s
                {filter_query}
                {metric_type_filter}
                AND deleted=0
            {order_query}
            {limit_query}
            {offset_query}
        """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query, params)
    return results


def calculate_performance_processing(org_id, model_id, dataset, metric):
    start_date = dataset["start_date"]
    end_date = add_one_day_in_date(dataset["end_date"])
    # agg_by = dataset["agg_by"]
    tags = dataset["tags"] if "tags" in dataset else []
    prep_filters, params = event_filters(
        org_id, model_id, dataset, start_date, end_date, end_date
    )
    params["metric_score_key"] = f"metric_{metric.id}_score"

    if len(tags) > 0:
        params["metric_tags_key"] = f"{metric.id}_tags"
        params["tags"] = value_list(tags)
        prep_filters.append(
            " arrayAll(x -> has(splitByString(';', EvalResults.Value[indexOf(EvalResults.Key, %(metric_tags_key)s)]), x), %(tags)s) "
        )

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    metric_type_filter = ""

    if metric.metric_type == Metric.MetricTypes.STEPWISE_MODEL_INFERENCE:
        metric_type_filter = " AND  has(Features.Key, 'node_id') "

    if metric.metric_type == Metric.MetricTypes.WHOLE_USER_OUTPUT:
        metric_type_filter = " AND NOT has(Features.Key, 'node_id') "

    final_query = ""
    # if metric == "accuracy":
    final_query = f"""
            SELECT
                COUNT(*) AS total_records,
                COUNTIf(metric_score = '') AS empty_metric_score_records
            FROM (
                SELECT
                    arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(metric_score_key)s)) AS metric_score
                FROM events
                WHERE EventDateTime BETWEEN %(start_date)s AND %(end_date)s
                    {filter_query}
                    {metric_type_filter}
                    AND deleted = 0
            )
        """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query, params)
    return results


def get_comparison_sign(desc):
    if desc == "equalTo":
        return " = "
    if desc == "notEqualTo":
        return " != "
    if desc == "greaterThan":
        return " > "
    if desc == "lessThan":
        return " < "
    if desc == "greaterThanEqualTo":
        return " >= "
    if desc == "lessThanEqualTo":
        return " <= "


def get_conversion_function(val_type):
    if val_type == "number":
        return "toFloat32"

    if val_type == "string":
        return "toString"


def get_performance_details_query(
    org_id, model_id, dataset, filters, agg_by, start_date, end_date
):
    metricId = dataset["metric_id"]

    prep_filters, params = event_filters(
        org_id,
        model_id,
        dataset,
        start_date,
        end_date,
        add_one_day_in_date(end_date),
    )
    params["agg_by"] = agg_by
    params["metric_score_key"] = f"metric_{metricId}_score"

    for filter_index, filter in enumerate(filters):
        query = _filter_condition(filter_index, filter, metricId, params)

        if query.strip() != "":
            prep_filters.append(query)

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    final_query = f"""
        WITH
            toDateTime(%(start_date)s) AS start_date,
            toDateTime(%(end_date)s) AS end_date,
            CASE
                WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(start_date)
                WHEN %(agg_by)s = 'daily' THEN toDate(start_date)
                WHEN %(agg_by)s = 'weekly' THEN toStartOfWeek(toDate(start_date))
                WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(start_date)
            END AS interval_start,
            CASE
                WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(end_date)
                WHEN %(agg_by)s = 'daily' THEN toDate(end_date)
                WHEN %(agg_by)s = 'weekly' THEN toLastDayOfWeek(toDate(end_date))
                WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(end_date)
            END AS interval_end,
            CASE
                WHEN %(agg_by)s = 'hourly' THEN 'hour'
                WHEN %(agg_by)s = 'daily' THEN 'day'
                WHEN %(agg_by)s = 'weekly' THEN 'week'
                WHEN %(agg_by)s = 'monthly' THEN 'month'
            END AS interval_type

        SELECT
            time_series.time_interval,
            (event_data.correct_predictions / 10) * 100 AS correct_predictions_percentage
        FROM (
            SELECT
                arrayJoin(arrayMap(x -> toDateTime(x),
                    range(toUInt32(interval_start), toUInt32(interval_end),
                        CASE
                            WHEN interval_type = 'hour' THEN 3600
                            WHEN interval_type = 'day' THEN 86400
                            WHEN interval_type = 'week' THEN 604800
                            WHEN interval_type = 'month' THEN 2592000
                        END
                    )
                )) AS time_interval
        ) AS time_series
        LEFT JOIN (
            SELECT
                agg_time AS time_interval,
                COUNT() AS total_events,
                AVG(if(isNotNull(metricValue) AND metricValue != '', toFloat32(metricValue), 0)) AS correct_predictions
            FROM (
                SELECT
                    UUID,
                    CASE
                        WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(EventDateTime)
                        WHEN %(agg_by)s = 'daily' THEN toDate(EventDate)
                        WHEN %(agg_by)s = 'weekly' THEN toStartOfWeek(EventDate)
                        WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(EventDate)
                    END AS agg_time,
                    arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(metric_score_key)s)) AS metricValue
                FROM events
                WHERE EventDateTime BETWEEN %(start_date)s AND %(end_date)s
                    {filter_query} AND has(EvalResults.Key, %(metric_score_key)s)
            )
            GROUP BY time_interval
        ) AS event_data ON time_series.time_interval = event_data.time_interval
        ORDER BY time_series.time_interval

    """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query, params)
    return results


def get_all_tags_distribution(
    org_id, model_id, dataset, filters, agg_by, start_date, end_date, tag_type
):
    metric_id = dataset.get("metric_id", "")

    prep_filters, params = event_filters(
        org_id,
        model_id,
        dataset,
        start_date,
        end_date,
        add_one_day_in_date(end_date),
    )
    params["agg_by"] = agg_by
    params["metric_tags_key"] = f"metric_{metric_id}_tags"
    params["tag_prefix"] = "POSITIVE" if tag_type == "good" else "NEGATIVE"

    for filter_index, filter in enumerate(filters):
        query = _filter_condition(filter_index, filter, metric_id, params)

        if query.strip() != "":
            prep_filters.append(query)

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    final_query = f"""
        WITH
            toDateTime(%(start_date)s) AS start_date,
            toDateTime(%(end_date)s) AS end_date,
            CASE
                WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(start_date)
                WHEN %(agg_by)s = 'daily' THEN toDate(start_date)
                WHEN %(agg_by)s = 'weekly' THEN toStartOfWeek(toDate(start_date))
                WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(start_date)
            END AS interval_start,
            CASE
                WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(end_date)
                WHEN %(agg_by)s = 'daily' THEN toDate(end_date)
                WHEN %(agg_by)s = 'weekly' THEN toLastDayOfWeek(toDate(end_date))
                WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(end_date)
            END AS interval_end,
            CASE
                WHEN %(agg_by)s = 'hourly' THEN 'hour'
                WHEN %(agg_by)s = 'daily' THEN 'day'
                WHEN %(agg_by)s = 'weekly' THEN 'week'
                WHEN %(agg_by)s = 'monthly' THEN 'month'
            END AS interval_type

        SELECT
            time_series.time_interval,
            (event_data.correct_predictions / 1) * 1 AS correct_predictions_percentage
        FROM (
            SELECT
                arrayJoin(arrayMap(x -> toDateTime(x),
                    range(toUInt32(interval_start), toUInt32(interval_end),
                        CASE
                            WHEN interval_type = 'hour' THEN 3600
                            WHEN interval_type = 'day' THEN 86400
                            WHEN interval_type = 'week' THEN 604800
                            WHEN interval_type = 'month' THEN 2592000
                        END
                    )
                )) AS time_interval
        ) AS time_series
        LEFT JOIN (
            SELECT
                agg_time AS time_interval,
                COUNT() AS total_events,
                sum(arraySum(metricValue)) AS correct_predictions
            FROM (
                SELECT
                    UUID,
                    CASE
                        WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(EventDateTime)
                        WHEN %(agg_by)s = 'daily' THEN toDate(EventDate)
                        WHEN %(agg_by)s = 'weekly' THEN toStartOfWeek(EventDate)
                        WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(EventDate)
                    END AS agg_time,
                    arrayMap(
                        x -> length(arrayFilter(x -> startsWith(x, %(tag_prefix)s), splitByString(';', arrayElement(EvalResults.Value, indexOf(EvalResults.Key, toString(x))) ))),
                        [%(metric_tags_key)s]
                    ) AS metricValue
                FROM events
                WHERE EventDateTime BETWEEN %(start_date)s AND %(end_date)s
                    {filter_query} AND has(EvalResults.Key, %(metric_tags_key)s)
            )
            GROUP BY time_interval
        ) AS event_data ON time_series.time_interval = event_data.time_interval
        ORDER BY time_series.time_interval
    """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query, params)
    return results


def get_top_tags_distribution(
    org_id, model_id, dataset, filters, start_date, end_date, tag_type
):
    metric_id = dataset.get("metric_id", "")

    prep_filters, params = event_filters(
        org_id,
        model_id,
        dataset,
        start_date,
        end_date,
        add_one_day_in_date(end_date),
    )
    params["metric_tags_key"] = f"metric_{metric_id}_tags"
    params["tag_prefix"] = "POSITIVE" if tag_type == "good" else "NEGATIVE"

    for filter_index, filter in enumerate(filters):
        query = _filter_condition(filter_index, filter, metric_id, params)

        if query.strip() != "":
            prep_filters.append(query)

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    final_query = f"""
        SELECT
            tag,
            COUNT(*) AS tag_count
        FROM (
            SELECT
                UUID,
                arrayJoin(
                    arrayFlatten(
                        arrayMap(
                            x -> splitByString(';', arrayElement(EvalResults.Value, indexOf(EvalResults.Key, toString(x)))),
                            [%(metric_tags_key)s]
                        )
                    )
                ) AS tag
            FROM events
            WHERE startsWith(tag, %(tag_prefix)s)
            {filter_query} AND has(EvalResults.Key, %(metric_tags_key)s)
        )
        GROUP BY tag
        ORDER BY tag_count DESC
        LIMIT 10
    """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query, params)
    return results
