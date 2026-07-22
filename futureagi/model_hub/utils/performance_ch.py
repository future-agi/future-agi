from model_hub.models.metric import Metric
from model_hub.utils.constant import journey_operator_map
from tfc.utils.clickhouse import ClickHouseClientSingleton
from tfc.utils.functions import add_one_day_in_date
from tfc.utils.types import Environments


def calculate_performance(
    org_id, model_id, dataset, metric, filters, positive_class=None, k=None
):
    version = dataset["version"]
    environment = Environments.convert_to_type(dataset["environment"])
    start_date = dataset["start_date"]
    end_date = add_one_day_in_date(dataset["end_date"])
    agg_by = dataset["agg_by"]
    tags = dataset["tags"] if "tags" in dataset else []

    prep_filters = [
        f" AIModel = '{model_id}' ",
        f" OrgID = '{org_id}' ",
        f" ModelVersion = '{version}' ",
        f" Environment = {environment} ",
        f" EventDateTime BETWEEN '{start_date}' AND '{end_date}' ",
        " deleted = 0 ",
    ]

    additional_select = []

    if len(tags) > 0:
        prep_filters.append(
            f" hasAll(splitByString(';', EvalResults.Value[indexOf(EvalResults.Key, 'metric_{metric.id}_tags')]), {tags}) "
        )

    for filter_index, filter in enumerate(filters):
        # filter_type = filter["type"]
        # property = filter["property"]
        comparison = get_comparison_sign(filter["comparison"])
        dataset_to_filter_version = filter["dataset"]["version"]
        # dataset_to_filter_environment = filter["dataset"]["environment"]
        filter_vals = filter["values"]
        conversion_function = get_conversion_function(filter["dataType"])

        feat_name = f"filter_{filter_index}"

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

        for filter_val in filter_vals:
            query_arr.append(
                f" {feat_name} {comparison} {conversion_function}('{filter_val}') "
            )

        query = " OR ".join(query_arr)
        query = f"""(
            ({query}) AND (
                AIModel = '{model_id}',
                OrgID = '{org_id}',
                ModelVersion = '{dataset_to_filter_version}',
                Environment = '{environment}'
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
            toDateTime('{start_date}') AS start_date,
            toDateTime('{end_date}') AS end_date,
            CASE
                WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(start_date)
                WHEN '{agg_by}' = 'daily' THEN toDate(start_date)
                WHEN '{agg_by}' = 'weekly' THEN toStartOfWeek(toDate(start_date))
                WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(start_date)
            END AS interval_start,
            CASE
                WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(end_date)
                WHEN '{agg_by}' = 'daily' THEN toDate(end_date)
                WHEN '{agg_by}' = 'weekly' THEN toLastDayOfWeek(toDate(end_date))
                WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(end_date)
            END AS interval_end,
            CASE
                WHEN '{agg_by}' = 'hourly' THEN 'hour'
                WHEN '{agg_by}' = 'daily' THEN 'day'
                WHEN '{agg_by}' = 'weekly' THEN 'week'
                WHEN '{agg_by}' = 'monthly' THEN 'month'
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
                        WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(EventDateTime)
                        WHEN '{agg_by}' = 'daily' THEN toDate(EventDate)
                        WHEN '{agg_by}' = 'weekly' THEN toStartOfWeek(EventDate)
                        WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(EventDate)
                    END AS agg_time,
                    arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric.id}_score')) AS metricValue
                    {select_query}
                FROM events
                WHERE EventDateTime BETWEEN '{start_date}' AND '{end_date}'
                    {filter_query}
                    {metric_type_filter}
            )
            GROUP BY time_interval
        ) AS event_data ON time_series.time_interval = event_data.time_interval
        ORDER BY time_series.time_interval

    """

    # if metric == "":

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute(final_query)
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
    version = dataset["version"]
    environment = Environments.convert_to_type(dataset["environment"])
    metric_id = dataset["metric_id"]

    prep_filters = [
        f" AIModel = '{model_id}' ",
        f" OrgID = '{org_id}' ",
        f" ModelVersion = '{version}' ",
        f" Environment = {environment} ",
        f" EventDateTime BETWEEN '{start_date}' AND '{add_one_day_in_date(end_date)}' ",
        " deleted = 0 ",
    ]

    for _filter_index, filter in enumerate(filters):
        filter_type = filter["type"]
        filter_data_type = filter["datatype"]
        filter_key = filter["key"]
        filter_value = filter["values"]
        filter_operator = journey_operator_map[filter["operator"]]
        filter_key_id = filter["key_id"]

        query = ""
        if filter_type == "property":
            if filter_data_type == "string":
                query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))),{filter_value})"
                query = (
                    f"arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                IN (SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                AS conv_id FROM events WHERE hasAll(splitByString(';',arrayElement(EvalResults.Value, \
                indexOf(EvalResults.Key, '{filter_key}'))),{filter_value}) \
                AND NOT has(Features.Key, 'node_id') AND deleted = 0)"
                )
            else:
                query = f"toFloat32(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))) {filter_operator} {filter_value[0]}"

        elif filter_type == "performanceMetric":
            if filter_value[0] == "":
                continue
            filter_value[0] = str(float(filter_value[0]) / 10)
            metric_value = f"arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{filter_key_id}_score'))"
            query = f"if(isNotNull({metric_value}) AND {metric_value} != '', toFloat32({metric_value}), 0) {filter_operator} {filter_value[0]}"
        elif filter_type == "performanceTag":
            query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric_id}_tags'))),{filter_value})"

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
        limit_query = f"LIMIT {limit + 1}"
        offset_query = f"OFFSET {offset or 0}"

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
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric_id}_score')) AS raw_metric_score
            SELECT
                UUID,
                EventDateTime,
                if(isNotNull(raw_metric_score) AND raw_metric_score != '', toFloat32(raw_metric_score), 0) AS metric_score,
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric_id}_explanation')) AS metric_explanation,
                arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS input_node_id,
                arrayElement(PredictionLabel.Value, indexOf(PredictionLabel.Key, 'node_id')) AS output_node_id,
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric_id}_tags')) AS metric_tags,
                arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) AS conversation_id
            FROM events
            WHERE EventDateTime BETWEEN '{start_date}' AND '{end_date}'
                {filter_query}
                {metric_type_filter}
                AND deleted=0
            {order_query}
            {limit_query}
            {offset_query}
        """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute(final_query)
    return results


def calculate_performance_processing(org_id, model_id, dataset, metric):
    version = dataset["version"]
    environment = Environments.convert_to_type(dataset["environment"])
    start_date = dataset["start_date"]
    end_date = add_one_day_in_date(dataset["end_date"])
    # agg_by = dataset["agg_by"]
    tags = dataset["tags"] if "tags" in dataset else []
    prep_filters = [
        f" AIModel = '{model_id}' ",
        f" OrgID = '{org_id}' ",
        f" ModelVersion = '{version}' ",
        f" Environment = {environment} ",
        f" EventDateTime BETWEEN '{start_date}' AND '{end_date}' ",
        " deleted = 0 ",
    ]

    if len(tags) > 0:
        prep_filters.append(
            f" arrayAll(x -> has(splitByString(';', EvalResults.Value[indexOf(EvalResults.Key, '{metric.id}_tags')]), x), {tags}) "
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
                    arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric.id}_score')) AS metric_score
                FROM events
                WHERE EventDateTime BETWEEN '{start_date}' AND '{end_date}'
                    {filter_query}
                    {metric_type_filter}
                    AND deleted = 0
            )
        """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute(final_query)
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
    version = dataset["version"]
    environment = Environments.convert_to_type(dataset["environment"])
    metricId = dataset["metric_id"]

    prep_filters = [
        f" AIModel = '{model_id}' ",
        f" OrgID = '{org_id}' ",
        f" ModelVersion = '{version}' ",
        f" Environment = {environment} ",
        f" EventDateTime BETWEEN '{start_date}' AND '{add_one_day_in_date(end_date)}' ",
        " deleted = 0 ",
    ]

    for _filter_index, filter in enumerate(filters):
        filter_type = filter["type"]
        filter_data_type = filter["datatype"]
        filter_key = filter["key"]
        filter_value = filter["values"]
        filter_operator = journey_operator_map[filter["operator"]]
        filter_key_id = filter["key_id"]

        query = ""
        if filter_type == "property":
            if filter_data_type == "string":
                query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))),{filter_value})"
                query = (
                    f"arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                IN (SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                AS conv_id FROM events WHERE hasAll(splitByString(';',arrayElement(EvalResults.Value, \
                indexOf(EvalResults.Key, '{filter_key}'))),{filter_value}) \
                AND NOT has(Features.Key, 'node_id') AND deleted = 0)"
                )
            else:
                query = f"toFloat32(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))) {filter_operator} {filter_value[0]}"

        elif filter_type == "performanceMetric":
            if filter_value[0] == "":
                continue
            filter_value[0] = str(float(filter_value[0]) / 10)
            metric_value = f"arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{filter_key_id}_score'))"
            query = f"if(isNotNull({metric_value}) AND {metric_value} != '', toFloat32({metric_value}), 0) {filter_operator} {filter_value[0]}"
        elif filter_type == "performanceTag":
            query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metricId}_tags'))),{filter_value})"

        if query.strip() != "":
            prep_filters.append(query)

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    final_query = f"""
        WITH
            toDateTime('{start_date}') AS start_date,
            toDateTime('{end_date}') AS end_date,
            CASE
                WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(start_date)
                WHEN '{agg_by}' = 'daily' THEN toDate(start_date)
                WHEN '{agg_by}' = 'weekly' THEN toStartOfWeek(toDate(start_date))
                WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(start_date)
            END AS interval_start,
            CASE
                WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(end_date)
                WHEN '{agg_by}' = 'daily' THEN toDate(end_date)
                WHEN '{agg_by}' = 'weekly' THEN toLastDayOfWeek(toDate(end_date))
                WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(end_date)
            END AS interval_end,
            CASE
                WHEN '{agg_by}' = 'hourly' THEN 'hour'
                WHEN '{agg_by}' = 'daily' THEN 'day'
                WHEN '{agg_by}' = 'weekly' THEN 'week'
                WHEN '{agg_by}' = 'monthly' THEN 'month'
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
                        WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(EventDateTime)
                        WHEN '{agg_by}' = 'daily' THEN toDate(EventDate)
                        WHEN '{agg_by}' = 'weekly' THEN toStartOfWeek(EventDate)
                        WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(EventDate)
                    END AS agg_time,
                    arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metricId}_score')) AS metricValue
                FROM events
                WHERE EventDateTime BETWEEN '{start_date}' AND '{end_date}'
                    {filter_query} AND has(EvalResults.Key, 'metric_{metricId}_score')
            )
            GROUP BY time_interval
        ) AS event_data ON time_series.time_interval = event_data.time_interval
        ORDER BY time_series.time_interval

    """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute(final_query)
    return results


def get_all_tags_distribution(
    org_id, model_id, dataset, filters, agg_by, start_date, end_date, tag_type
):
    version = dataset["version"]
    environment = Environments.convert_to_type(dataset["environment"])
    metric_id = dataset.get("metric_id", "")

    prep_filters = [
        f" AIModel = '{model_id}' ",
        f" OrgID = '{org_id}' ",
        f" ModelVersion = '{version}' ",
        f" Environment = {environment} ",
        f" EventDateTime BETWEEN '{start_date}' AND '{add_one_day_in_date(end_date)}' ",
        " deleted = 0 ",
    ]

    for _filter_index, filter in enumerate(filters):
        filter_type = filter["type"]
        filter_data_type = filter["datatype"]
        filter_key = filter["key"]
        filter_value = filter["values"]
        filter_operator = journey_operator_map[filter["operator"]]
        filter_key_id = filter["key_id"]

        if filter_type == "property":
            if filter_data_type == "string":
                query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))),{filter_value})"
                query = (
                    f"arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                IN (SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                AS conv_id FROM events WHERE hasAll(splitByString(';',arrayElement(EvalResults.Value, \
                indexOf(EvalResults.Key, '{filter_key}'))),{filter_value}) \
                AND NOT has(Features.Key, 'node_id') AND deleted = 0)"
                )
            else:
                query = f"toFloat32(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))) {filter_operator} {filter_value[0]}"

        elif filter_type == "performanceMetric":
            if filter_value[0] == "":
                continue
            filter_value[0] = str(float(filter_value[0]) / 10)
            metric_value = f"arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{filter_key_id}_score'))"
            query = f"if(isNotNull({metric_value}) AND {metric_value} != '', toFloat32({metric_value}), 0) {filter_operator} {filter_value[0]}"
        elif filter_type == "performanceTag":
            query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric_id}_tags'))),{filter_value})"

        prep_filters.append(query)

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    final_query = f"""
        WITH
            toDateTime('{start_date}') AS start_date,
            toDateTime('{end_date}') AS end_date,
            CASE
                WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(start_date)
                WHEN '{agg_by}' = 'daily' THEN toDate(start_date)
                WHEN '{agg_by}' = 'weekly' THEN toStartOfWeek(toDate(start_date))
                WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(start_date)
            END AS interval_start,
            CASE
                WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(end_date)
                WHEN '{agg_by}' = 'daily' THEN toDate(end_date)
                WHEN '{agg_by}' = 'weekly' THEN toLastDayOfWeek(toDate(end_date))
                WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(end_date)
            END AS interval_end,
            CASE
                WHEN '{agg_by}' = 'hourly' THEN 'hour'
                WHEN '{agg_by}' = 'daily' THEN 'day'
                WHEN '{agg_by}' = 'weekly' THEN 'week'
                WHEN '{agg_by}' = 'monthly' THEN 'month'
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
                        WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(EventDateTime)
                        WHEN '{agg_by}' = 'daily' THEN toDate(EventDate)
                        WHEN '{agg_by}' = 'weekly' THEN toStartOfWeek(EventDate)
                        WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(EventDate)
                    END AS agg_time,
                    arrayMap(
                        x -> length(arrayFilter(x -> startsWith(x, {"'POSITIVE'" if tag_type == "good" else "'NEGATIVE'"}), splitByString(';', arrayElement(EvalResults.Value, indexOf(EvalResults.Key, toString(x))) ))),
                        ['metric_{metric_id}_tags']
                    ) AS metricValue
                FROM events
                WHERE EventDateTime BETWEEN '{start_date}' AND '{end_date}'
                    {filter_query} AND has(EvalResults.Key, 'metric_{metric_id}_tags')
            )
            GROUP BY time_interval
        ) AS event_data ON time_series.time_interval = event_data.time_interval
        ORDER BY time_series.time_interval
    """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute(final_query)
    return results


def get_top_tags_distribution(
    org_id, model_id, dataset, filters, start_date, end_date, tag_type
):
    version = dataset["version"]
    environment = Environments.convert_to_type(dataset["environment"])
    metric_id = dataset.get("metric_id", "")

    prep_filters = [
        f" AIModel = '{model_id}' ",
        f" OrgID = '{org_id}' ",
        f" ModelVersion = '{version}' ",
        f" Environment = {environment} ",
        f" EventDateTime BETWEEN '{start_date}' AND '{add_one_day_in_date(end_date)}' ",
        " deleted = 0 ",
    ]

    for _filter_index, filter in enumerate(filters):
        filter_type = filter["type"]
        filter_data_type = filter["datatype"]
        filter_key = filter["key"]
        filter_value = filter["values"]
        filter_operator = journey_operator_map[filter["operator"]]
        filter_key_id = filter["key_id"]

        if filter_type == "property":
            if filter_data_type == "string":
                query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))),{filter_value})"
                query = (
                    f"arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                IN (SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                AS conv_id FROM events WHERE hasAll(splitByString(';',arrayElement(EvalResults.Value, \
                indexOf(EvalResults.Key, '{filter_key}'))),{filter_value}) \
                AND NOT has(Features.Key, 'node_id') AND deleted = 0)"
                )
            else:
                query = f"toFloat32(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))) {filter_operator} {filter_value[0]}"

        elif filter_type == "performanceMetric":
            if filter_value[0] == "":
                continue
            filter_value[0] = str(float(filter_value[0]) / 10)
            metric_value = f"arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{filter_key_id}_score'))"
            query = f"if(isNotNull({metric_value}) AND {metric_value} != '', toFloat32({metric_value}), 0) {filter_operator} {filter_value[0]}"
        elif filter_type == "performanceTag":
            query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric_id}_tags'))),{filter_value})"

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
                            ['metric_{metric_id}_tags']
                        )
                    )
                ) AS tag
            FROM events
            WHERE startsWith(tag, {"'POSITIVE'" if tag_type == "good" else "'NEGATIVE'"})
            {filter_query} AND has(EvalResults.Key, 'metric_{metric_id}_tags')
        )
        GROUP BY tag
        ORDER BY tag_count DESC
        LIMIT 10
    """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute(final_query)
    return results
