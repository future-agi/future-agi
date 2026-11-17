from datetime import datetime, timedelta

from model_hub.models.dataset_insight_meta import DatasetInsightMeta
from model_hub.utils.constant import journey_operator_map
from model_hub.utils.performance_ch import event_filters, number_value, value_list
from tfc.utils.clickhouse import ClickHouseClientSingleton

# A count filter compares with one of these operators, kept as fixed text.
COMPARISON_TEXT = {symbol: symbol for symbol in journey_operator_map.values()}


def addDays(date_string, count=1):
    # Convert the string to a datetime object
    date_obj = datetime.strptime(date_string, "%Y-%m-%d %H:%M:%S")

    # Add one day
    new_date_obj = date_obj + timedelta(days=count)

    # Convert back to string if needed
    new_date_string = new_date_obj.strftime("%Y-%m-%d %H:%M:%S")

    return new_date_string


def get_insight_details(org_id, model_id, dataset, filters, metric):
    start_date = dataset["start_date"]
    end_date = addDays(dataset["end_date"])

    prep_filters, params = event_filters(
        org_id, model_id, dataset, start_date, end_date, end_date
    )
    params["agg_by"] = str(dataset["agg_by"])

    all_filters = filters + metric["filters"]

    for filter_index, filter in enumerate(all_filters):
        if (
            "type" not in filter
            or "data_type" not in filter
            or "key" not in filter
            or "value" not in filter
            or "operator" not in filter
        ):
            continue
        filter_type = filter["type"]
        filter_data_type = filter["data_type"]
        filter_value = filter["value"]
        filter_operator = journey_operator_map[filter["operator"]]
        name = f"filter_{filter_index}"

        if filter_type == "property":
            if filter_data_type == "string":
                params[f"{name}_key"] = str(filter["key"])
                params[f"{name}_values"] = value_list(filter_value)
                query = (
                    f"arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                IN (SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                AS conv_id FROM events WHERE hasAll(splitByString(';',arrayElement(EvalResults.Value, \
                indexOf(EvalResults.Key, %({name}_key)s))),%({name}_values)s) \
                AND NOT has(Features.Key, 'node_id') AND deleted = 0)"
                )

        else:
            filter_value[0] = str(float(filter_value[0]) / 10)
            params["metric_score_key"] = f"metric_{metric['database_id']}_score"
            params[f"{name}_value"] = float(filter_value[0])
            metric_value = "arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(metric_score_key)s))"
            query = f"if(isNotNull({metric_value}) AND {metric_value} != '', toFloat32({metric_value}), 0) {filter_operator} %({name}_value)s"

        prep_filters.append(query)

    select_query = ""
    metric_select = ""

    if metric["type"] == "property":
        # prep_filters.append(f"has(EvalResults.Key, '{metric['key']}')")
        select_query = "COUNT() AS total_events"
    else:
        select_query = "round(AVG(if(isNotNull(metricValue) AND metricValue != '', toFloat32(metricValue), 0)) * 10, 2) AS total_events"

    if metric["type"] == "performanceMetric":
        params["metric_score_key"] = f"metric_{metric['database_id']}_score"
        metric_select = ",arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %(metric_score_key)s)) AS metricValue"

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    final_query = ""

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
                {select_query}
            FROM (
                SELECT
                    UUID,
                    CASE
                        WHEN %(agg_by)s = 'hourly' THEN toStartOfHour(EventDateTime)
                        WHEN %(agg_by)s = 'daily' THEN toDate(EventDate)
                        WHEN %(agg_by)s = 'weekly' THEN toLastDayOfWeek(EventDate)
                        WHEN %(agg_by)s = 'monthly' THEN toStartOfMonth(EventDate)
                    END AS agg_time
                    {metric_select}
                FROM events
                WHERE EventDateTime BETWEEN %(start_date)s AND %(end_date)s
                AND has(Features.Key,'node_id')
                    {filter_query}
            )
            GROUP BY time_interval
        ) AS event_data ON time_series.time_interval = event_data.time_interval
        ORDER BY time_series.time_interval

    """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query, params)
    return results


def get_insight_details_count(
    org_id,
    model_id,
    dataset,
    filters,
):
    start_date = dataset["start_date"]
    end_date = dataset["end_date"]
    # agg_by = dataset["agg_by"]

    prep_filters, params = event_filters(
        org_id, model_id, dataset, start_date, end_date, end_date
    )

    for filter_index, filter in enumerate(filters):
        filter_data_type = filter["data_type"]
        filter_value = filter["value"]
        operator = filter["operator"]
        name = f"filter_{filter_index}"
        params[f"{name}_key"] = str(filter["key"])

        if filter_data_type == "string":
            params[f"{name}_values"] = value_list(filter_value)
            query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %({name}_key)s))),%({name}_values)s)"
        else:
            if not isinstance(operator, str) or operator not in COMPARISON_TEXT:
                raise ValueError("Unknown filter operator.")
            filter_operator = COMPARISON_TEXT[operator]
            params[f"{name}_value"] = number_value(filter_value[0])
            query = f"toFloat32(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, %({name}_key)s))) {filter_operator} %({name}_value)s"

        prep_filters.append(query)

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    final_query = ""

    final_query = f"""
        SELECT
            COUNT() AS total_events
        FROM events
        WHERE EventDateTime BETWEEN %(start_date)s AND %(end_date)s
            {filter_query}
    """

    # print(final_query)

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query, params)
    return results


def get_insight_options(user_organization, insight):
    dataset = insight["datasets"][0]
    all_options = (
        DatasetInsightMeta.objects.filter(
            organization=user_organization,
            model=insight["model"],
            environment=dataset["environment"],
            version=dataset["model_version"],
        )
        .values("insight_options", "id")
        .first()
    )

    return all_options
