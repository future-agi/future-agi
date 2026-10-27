from datetime import datetime, timedelta

from model_hub.models.dataset_insight_meta import DatasetInsightMeta
from model_hub.utils.constant import journey_operator_map
from tfc.utils.clickhouse import ClickHouseClientSingleton
from tfc.utils.types import Environments


def addDays(date_string, count=1):
    # Convert the string to a datetime object
    date_obj = datetime.strptime(date_string, "%Y-%m-%d %H:%M:%S")

    # Add one day
    new_date_obj = date_obj + timedelta(days=count)

    # Convert back to string if needed
    new_date_string = new_date_obj.strftime("%Y-%m-%d %H:%M:%S")

    return new_date_string


def get_insight_details(org_id, model_id, dataset, filters, metric):
    version = dataset["version"]
    environment = Environments.convert_to_type(dataset["environment"])
    start_date = dataset["start_date"]
    end_date = addDays(dataset["end_date"])
    agg_by = dataset["agg_by"]

    prep_filters = [
        f" AIModel = '{model_id}' ",
        f" OrgID = '{org_id}' ",
        f" ModelVersion = '{version}' ",
        f" Environment = {environment} ",
        f" EventDateTime BETWEEN '{start_date}' AND '{end_date}' ",
        " deleted = 0 ",
    ]

    all_filters = filters + metric["filters"]

    for _filter_index, filter in enumerate(all_filters):
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
        filter_key = filter["key"]
        filter_value = filter["value"]
        filter_operator = journey_operator_map[filter["operator"]]

        if filter_type == "property":
            if filter_data_type == "string":
                query = f"arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                IN (SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
                AS conv_id FROM events WHERE hasAll(splitByString(';',arrayElement(EvalResults.Value, \
                indexOf(EvalResults.Key, '{filter_key}'))),{filter_value}) \
                AND NOT has(Features.Key, 'node_id') AND deleted = 0)"

        else:
            filter_value[0] = str(float(filter_value[0]) / 10)
            metric_value = f"arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric['database_id']}_score'))"
            query = f"if(isNotNull({metric_value}) AND {metric_value} != '', toFloat32({metric_value}), 0) {filter_operator} {filter_value[0]}"

        prep_filters.append(query)

    select_query = ""

    if metric["type"] == "property":
        # prep_filters.append(f"has(EvalResults.Key, '{metric['key']}')")
        query = ""
        if metric["data_type"] == "string":
            query = f"arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
            IN (SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) \
            AS conv_id FROM events WHERE has(EvalResults.Key, '{metric['key']}') \
            AND NOT has(Features.Key, 'node_id') AND deleted = 0"
        else:
            query = ""
            # query = f"toFloat32(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))) {filter_operator} {filter_value[0]}"

        select_query = "COUNT() AS total_events"
    else:
        select_query = "round(AVG(if(isNotNull(metricValue) AND metricValue != '', toFloat32(metricValue), 0)) * 10, 2) AS total_events"

    if len(prep_filters):
        filter_query = " AND " + "(" + " ) AND ( ".join(prep_filters) + ")"
    else:
        filter_query = ""

    final_query = ""

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
                        WHEN '{agg_by}' = 'hourly' THEN toStartOfHour(EventDateTime)
                        WHEN '{agg_by}' = 'daily' THEN toDate(EventDate)
                        WHEN '{agg_by}' = 'weekly' THEN toLastDayOfWeek(EventDate)
                        WHEN '{agg_by}' = 'monthly' THEN toStartOfMonth(EventDate)
                    END AS agg_time
                    {f",arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric['database_id']}_score')) AS metricValue" if metric["type"] == "performanceMetric" else ""}
                FROM events
                WHERE EventDateTime BETWEEN '{start_date}' AND '{end_date}'
                AND has(Features.Key,'node_id')
                    {filter_query}
            )
            GROUP BY time_interval
        ) AS event_data ON time_series.time_interval = event_data.time_interval
        ORDER BY time_series.time_interval

    """

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query)
    return results


def get_insight_details_count(
    org_id,
    model_id,
    dataset,
    filters,
):
    version = dataset["version"]
    environment = Environments.convert_to_type(dataset["environment"])
    start_date = dataset["start_date"]
    end_date = dataset["end_date"]
    # agg_by = dataset["agg_by"]

    prep_filters = [
        f" AIModel = '{model_id}' ",
        f" OrgID = '{org_id}' ",
        f" ModelVersion = '{version}' ",
        f" Environment = {environment} ",
        f" EventDateTime BETWEEN '{start_date}' AND '{end_date}' ",
        " deleted = 0 ",
    ]

    for _filter_index, filter in enumerate(filters):
        filter_data_type = filter["data_type"]
        filter_key = filter["key"]
        filter_value = filter["value"]
        filter_operator = filter["operator"]

        if filter_data_type == "string":
            query = f"hasAll(splitByString(';',arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))),{filter_value})"
        else:
            query = f"toFloat32(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, '{filter_key}'))) {filter_operator} {filter_value[0]}"

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
        WHERE EventDateTime BETWEEN '{start_date}' AND '{end_date}'
            {filter_query}
    """

    # print(final_query)

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute_read(final_query)
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
