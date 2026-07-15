import uuid

from model_hub.models.metric import Metric
from tfc.utils.clickhouse import ClickHouseClientSingleton
from tfc.utils.types import ClickhouseDatatypes, EventTypes


def get_event_id_from_prediction_id(prediction_id):
    query = "SELECT UUID from events where PredictionId = %(prediction_id)s limit 1"

    clickhouse_client = ClickHouseClientSingleton()  # Your ClickHouse client class
    results = clickhouse_client.execute(query, {"prediction_id": prediction_id})

    return results


def upsert_record(
    table_name,
    primary_key_field,
    primary_key_value,
    updated_values,
    columns,
    _uuid=None,
):
    # print("upserting")
    client = ClickHouseClientSingleton()
    columns_list = get_table_columns(table_name)
    # Step 1: Fetch the existing record
    select_query = f"""
        SELECT *
        FROM {table_name}
        WHERE {primary_key_field} = %(primary_key_value)s AND deleted = 0
    """
    existing_record = client.execute(
        select_query, {"primary_key_value": primary_key_value}
    )

    if existing_record:
        # Convert the record to a dictionary and merge with updated_values
        existing_record_dict = dict(zip(columns_list, existing_record[0], strict=False))
        merged_values = {**existing_record_dict, **updated_values, "deleted": 0}

        # Mark the existing record as deleted
        delete_query = f"""
        ALTER TABLE {table_name}
        UPDATE deleted = 1
        WHERE {primary_key_field} = %(primary_key_value)s AND deleted = 0
        """
        client.execute(delete_query, {"primary_key_value": primary_key_value})
    else:
        # If no existing record, prepare to insert a new row
        merged_values = updated_values
        if primary_key_field not in merged_values:
            merged_values[primary_key_field] = primary_key_value
        merged_values["deleted"] = (
            0  # Ensure 'deleted' flag is set to 0 for new records
        )

    if not _uuid:
        merged_values["UUID"] = str(uuid.uuid4())
    else:
        merged_values["UUID"] = str(_uuid)

    # Step 2: Insert the new or updated record
    fields = ", ".join(merged_values.keys())
    placeholders = ", ".join(["%(" + key + ")s" for key in merged_values.keys()])
    insert_query = f"""
        INSERT INTO {table_name} ({fields})
        VALUES ({placeholders})
    """

    client.execute(insert_query, merged_values)


def get_table_columns(table_name):
    client = ClickHouseClientSingleton()
    query = f"DESCRIBE TABLE {table_name}"
    columns = client.execute(query)
    return [col[0] for col in columns]


def update_eval_record(
    table_name, primary_key_field, primary_key_value, updated_values, columns
):
    client = ClickHouseClientSingleton()
    merged_values = {}
    # Step 1: Fetch the existing record
    columns_list = get_table_columns(table_name)
    select_query = f"""
        SELECT *
        FROM {table_name}
        WHERE {primary_key_field} = %(primary_key_value)s AND deleted = 0
    """
    existing_record = client.execute(
        select_query, {"primary_key_value": primary_key_value}
    )

    # print(select_query, {"primary_key_value": primary_key_value})

    if existing_record:
        # print("existing_record", existing_record)
        # Convert the record to a dictionary and merge with updated_values
        new_eval_values = {}
        existing_record_dict = dict(zip(columns_list, existing_record[0], strict=False))

        for k, v, t in zip(
            existing_record_dict["EvalResults.Key"],
            existing_record_dict["EvalResults.Value"],
            existing_record_dict["EvalResults.DataType"],
            strict=False,
        ):
            new_eval_values[k] = (v, t)

        for updated_value_key, updated_value in updated_values.items():
            new_eval_values[updated_value_key] = (
                updated_value,
                ClickhouseDatatypes.get_data_type(updated_value),
            )

        existing_record_dict["EvalResults.Key"] = list(new_eval_values.keys())
        existing_record_dict["EvalResults.Value"] = [
            str(v[0]) for v in new_eval_values.values()
        ]
        existing_record_dict["EvalResults.DataType"] = [
            v[1] for v in new_eval_values.values()
        ]

        merged_values = {**existing_record_dict, "deleted": 0}

        # Mark the existing record as deleted
        delete_query = f"""
        ALTER TABLE {table_name}
        UPDATE deleted = 1
        WHERE {primary_key_field} = %(primary_key_value)s AND deleted = 0
        """
        client.execute(delete_query, {"primary_key_value": primary_key_value})
    else:
        raise ValueError(
            f"{table_name} has no record with {primary_key_field}={primary_key_value}"
        )

    merged_values["UUID"] = str(uuid.uuid4())

    # Step 2: Insert the new or updated record
    fields = ", ".join(merged_values.keys())
    placeholders = ", ".join(["%(" + key + ")s" for key in merged_values.keys()])
    insert_query = f"""
        INSERT INTO {table_name} ({fields})
        VALUES ({placeholders})
    """

    client.execute(insert_query, merged_values)


def create_empty_conversation(table_name, conversation_id, columns, _uuid):
    client = ClickHouseClientSingleton()
    merged_values = {}

    columns_list = get_table_columns(table_name)
    select_query = f"""
        SELECT *
        FROM {table_name}
        WHERE arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) = %(primary_key_value)s
        AND deleted = 0
        limit 1
    """
    existing_record = client.execute(
        select_query, {"primary_key_value": conversation_id}
    )

    # print("existing_record",existing_record, len(existing_record))

    existing_record_dict = dict(zip(columns_list, existing_record[0], strict=False))
    existing_record_dict["Features.Key"] = ["conversation_id"]
    existing_record_dict["Features.Value"] = [str(conversation_id)]
    existing_record_dict["Features.DataType"] = [
        ClickhouseDatatypes.get_data_type(conversation_id)
    ]
    existing_record_dict["Properties.Key"] = []
    existing_record_dict["Properties.Value"] = []
    existing_record_dict["ActualLabel.Key"] = []
    existing_record_dict["ActualLabel.Value"] = []
    existing_record_dict["ActualLabel.DataType"] = []
    existing_record_dict["PredictionLabel.Key"] = []
    existing_record_dict["PredictionLabel.Value"] = []
    existing_record_dict["PredictionLabel.DataType"] = []
    existing_record_dict["ShapValues.Key"] = []
    existing_record_dict["ShapValues.Value"] = []
    existing_record_dict["ShapValues.DataType"] = []
    existing_record_dict["EvalResults.Key"] = []
    existing_record_dict["EvalResults.Value"] = []
    existing_record_dict["EvalResults.DataType"] = []
    existing_record_dict["original_uuid"] = str(_uuid)

    merged_values = {**existing_record_dict, "deleted": 0}
    # merged_values = {
    #     "original_uuid": str(_uuid),
    #     "EventDate": existing_record[2],
    #     "EventDateTime": existing_record[3],
    #     "EventName": existing_record[4],
    #     "EventType": existing_record[5],
    #     "AIModel": existing_record[6],
    #     "OrgID": existing_record[7],
    #     "PredictionID": existing_record[8],  # prediction_id,
    #     "ModelVersion": existing_record[9],
    #     "BatchID": existing_record[10],
    #     "Environment": existing_record[11],
    #     "Properties.Key": [],
    #     "Properties.Value": [],
    #     "Properties.DataType": [],
    #     "Features.Key": ["conversation_id"],
    #     "Features.Value": [str(conversation_id)],
    #     "Features.DataType": [ClickhouseDatatypes.get_data_type(conversation_id)],
    #     "ActualLabel.Key": [],
    #     "ActualLabel.Value": [],
    #     "ActualLabel.DataType": [],
    #     "PredictionLabel.Key": [],
    #     "PredictionLabel.Value": [],
    #     "PredictionLabel.DataType": [],
    #     "ShapValues.Key": [],
    #     "ShapValues.Value": [],
    #     "ShapValues.DataType": [],
    #     "Tags.Key": existing_record[27],
    #     "Tags.Value": existing_record[28],
    #     "Tags.DataType": existing_record[29],
    #     # Temp
    #     "EvalResults.Key": [],
    #     "EvalResults.Value": [],
    #     "EvalResults.DataType": [],
    #     "Embedding": existing_record[33],
    #     "deleted": 0,
    # }

    if not _uuid:
        merged_values["UUID"] = str(uuid.uuid4())
    else:
        merged_values["UUID"] = str(_uuid)

    fields = ", ".join(merged_values.keys())
    placeholders = ", ".join(["%(" + key + ")s" for key in merged_values.keys()])
    insert_query = f"""
        INSERT INTO {table_name} ({fields})
        VALUES ({placeholders})
    """

    # print({"insert_query": insert_query, "merged_values": merged_values})

    client.execute(insert_query, merged_values)


def insert_record(table_name, updated_values):
    client = ClickHouseClientSingleton()

    merged_values = updated_values
    merged_values["deleted"] = 0
    merged_values["UUID"] = str(uuid.uuid4())

    fields = ", ".join(merged_values.keys())
    placeholders = ", ".join(["%(" + key + ")s" for key in merged_values.keys()])
    insert_query = f"""
        INSERT INTO {table_name} ({fields})
        VALUES ({placeholders})
    """

    client.execute(insert_query, merged_values)


def get_model_hourly_volume(org_id=None, model_ids=None, hours=24):
    if model_ids is None:
        model_ids = []
    hours = int(hours)
    if len(model_ids) == 0 and not org_id:
        return [], 0

    client = ClickHouseClientSingleton()
    where_clauses = [
        f"EventDateTime >= now() - INTERVAL {hours} HOUR",
        "has(Features.Key, 'node_id')",
        "deleted = 0",
    ]
    if len(model_ids) > 0:
        model_ids = ["'" + str(id) + "'" for id in model_ids]
        where_clauses.append(f"AIModel in ({', '.join(model_ids)})")
    else:
        where_clauses.append(f"OrgID = '{org_id}'")

    where_sql = "\n                            AND ".join(where_clauses)
    query = f"""
            SELECT
                seriesTime,
                COALESCE(SUM(recordsCount), 0) AS RecordCount
            FROM
                (
                    SELECT
                        arrayJoin(
                            arrayMap(x -> toStartOfHour(now()) - INTERVAL x HOUR, range({hours}))
                        ) AS seriesTime
                ) AS series
            LEFT JOIN
                (
                    SELECT
                        toStartOfHour(EventDateTime) AS EventHour,
                        COUNT(*) AS recordsCount
                    FROM events
                    WHERE {where_sql}
                    GROUP BY EventHour
                ) AS events
            ON series.seriesTime = events.EventHour
            GROUP BY seriesTime
            ORDER BY seriesTime;
        """

    data = client.execute(query)
    points = []
    total_count = 0
    for row in data:
        points.append(
            {
                "y": row[1],
                "x": row[0],
            }
        )
        total_count += row[1]

    return points, total_count


def get_model_volume(org_id=None, model_ids=None, days=30, hours=24):
    if model_ids is None:
        model_ids = []
    if len(model_ids) == 0 and not org_id:
        return

    client = ClickHouseClientSingleton()
    if len(model_ids) > 0:
        model_ids = ["'" + str(id) + "'" for id in model_ids]
        model_ids = ", ".join(model_ids)
        model_ids = f"({model_ids})"

        query = f"""
                SELECT
                    seriesDate,
                    COALESCE(recordsCount, 0) AS RecordCount
                FROM
                    (
                        SELECT
                            arrayJoin(arrayMap(x -> today() - x, range({days}))) AS seriesDate
                    ) AS series
                LEFT JOIN
                    (
                        SELECT
                            EventDate,
                            COUNT(EventDate) AS recordsCount
                        FROM events
                        WHERE
                            EventDate >= today() - {days}
                            AND AIModel in  {model_ids}
                            AND has(Features.Key, 'node_id')
                            AND deleted = 0
                        GROUP BY EventDate
                    ) AS events
                ON series.seriesDate = events.EventDate
                ORDER BY seriesDate;

        """

    else:
        query = f"""
            -- Select from the generated series
            SELECT
                seriesTime,
                -- If there's no match in the original table, return 0
                COALESCE(SUM(recordsCount), 0) AS RecordCount
            FROM
                (
                    -- Convert the generated numbers to timestamps
                    SELECT
                        arrayJoin(
                            arrayMap(x -> toStartOfHour(now()) - INTERVAL x HOUR, range({hours}))
                        ) AS seriesTime
                ) AS series
            LEFT JOIN
                (
                    SELECT
                        toStartOfHour(EventDateTime) AS EventHour,
                        COUNT(*) AS recordsCount
                    FROM events
                    WHERE EventDateTime >= now() - INTERVAL {hours} HOUR AND
                    OrgID = '{org_id}' AND
                    has(Features.Key, 'node_id') AND
                    deleted = 0
                    GROUP BY EventHour
                ) AS events
            ON series.seriesTime = events.EventHour
            GROUP BY seriesTime
            ORDER BY seriesTime;

        """

    # print("QUR", query)
    data = client.execute(query)
    points = []
    total_count = 0
    for row in data:
        points.append(
            {
                "y": row[1],
                "x": row[0],
            }
        )

        total_count += row[1]

    return points, total_count


def get_model_details(model, org):
    client = ClickHouseClientSingleton()
    metrics = Metric.objects.filter(model=model)

    dataset_query = f"""
        SELECT COUNT(UUID)
        FROM events
        WHERE OrgID = '{org.id}'
        AND AIModel = '{model.id}'
        AND EventType = '{str(EventTypes.MODEL_PREDICTION.value)}'
        AND deleted=0
    """

    # print(dataset_query)

    results = client.execute(dataset_query)
    # return {"is_metric_added": metrics.exists(), "isDatasetAdded": True}

    return {
        "is_metric_added": metrics.exists(),
        "is_dataset_added": results[0][0] > 0,
    }


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


def copy_modify_and_insert_rows(
    select_query, insert_table, modifications, modification_per_row=None
):
    # Connect to ClickHouse
    client = ClickHouseClientSingleton()

    # Execute the select query to get the rows
    data_rows = client.execute(select_query)

    # Retrieve column names and types
    columns_with_types = client.execute(f"DESCRIBE TABLE ({select_query})")
    column_names = [col[0] for col in columns_with_types]

    # Identify nested columns
    nested_columns = {}
    for column in column_names:
        if "." in column:
            base_col = column.split(".")[0]
            if base_col not in nested_columns:
                nested_columns[base_col] = []
            nested_columns[base_col].append(column)

    # List to hold the modified rows
    modified_rows = []

    # Loop through each row
    for row in data_rows:
        # Convert row to list for mutability
        new_row = list(row)
        new_modification = modifications
        if modification_per_row is not None:
            new_modification = modification_per_row(row)

        # Apply modifications based on column names
        for column_name, new_value in new_modification.items():
            if column_name in column_names:
                column_index = column_names.index(column_name)
                new_row[column_index] = new_value
            elif column_name in nested_columns:
                # Handle nested fields dynamically
                for sub_column in nested_columns[column_name]:
                    sub_column_index = column_names.index(sub_column)
                    new_row[sub_column_index] = []  # Empty the nested subfield

        # Generate a new UUID for each modified row
        uuid_index = column_names.index("UUID")
        new_row[uuid_index] = str(uuid.uuid4())

        # Append the modified row to the list
        modified_rows.append(tuple(new_row))  # Convert back to tuple for insertion

    # Prepare and execute the insert query
    client.execute(f"INSERT INTO {insert_table} VALUES", modified_rows)

    return modified_rows
