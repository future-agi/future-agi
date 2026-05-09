import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
from channels.db import database_sync_to_async
from dateutil.relativedelta import relativedelta
from django.db import connection

from sockets.consumer import DataConsumer
from tracer.utils.filters import FilterEngine

# from tracer.models.observation_span import ObservationSpan


class GraphDataConsumer(DataConsumer):
    async def receive_json(self, content):
        self.project_id = content.get("projectId")
        self.filters = content.get("filters", [])
        self.interval = content.get("interval", "hour")
        self.property = content.get("property", "")
        self.graph = content.get("graph", "")
        self.eval_id = (
            content.get("evalIds", [""])[0]
            if len(content.get("evalIds", [""])) > 0
            else ""
        )

        if not self.project_id:
            await self.close()
            return

        await self.send_metrics_data()

    async def send_metrics_data(self):
        if self.graph == "trace":
            await self.send_evaluation_data()

        if self.graph == "trace":
            await self.send_evaluation_data()

        elif self.graph == "charts":
            await self.send_traffic_data()
            await self.send_latency_data()
            await self.send_cost_data()
            await self.send_tokens_data()
            await self.send_evaluation_data()

    async def generate_complete_timeline(self, data, value_key):
        # Determine the start and end range based on filters
        start_time, end_time = None, None
        for filter_item in self.filters:
            filter_config = filter_item.get("filterConfig", {})
            filter_type = filter_config.get("filterType")
            filter_op = filter_config.get("filterOp")
            filter_value = filter_config.get("filterValue")

            if (
                filter_type == "datetime"
                and filter_op == "between"
                and isinstance(filter_value, list)
                and len(filter_value) == 2
            ):
                start_time, end_time = filter_value

        if not start_time or not end_time:
            return data

        start_time = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            minute=0, second=0
        )
        end_time = datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            minute=0, second=0
        )

        if self.interval == "week":
            start_time -= timedelta(days=start_time.weekday())
            end_time -= timedelta(days=end_time.weekday())

        if self.interval == "month":
            start_time = start_time.replace(day=1)
            end_time = (end_time.replace(day=1) + relativedelta(months=1)) - timedelta(
                days=1
            )

        if self.interval != "hour":
            start_time = start_time.replace(hour=0)
            end_time = end_time.replace(hour=0)

        # Determine the interval step
        interval_map = {
            "hour": timedelta(hours=1),
            "day": timedelta(days=1),
            "week": timedelta(weeks=1),
            "month": relativedelta(months=1),
        }
        step = interval_map.get(self.interval)  # Default to daily if unknown

        # Create a dict for easy lookup, normalizing timestamps
        existing_data = {
            datetime.strptime(entry["timestamp"], "%Y-%m-%dT%H:%M:%S")
            .replace(minute=0, second=0)
            .strftime("%Y-%m-%dT%H:%M:%S"): entry[value_key]
            for entry in data
        }

        # Generate full timeline
        filled_data = []
        current_time = start_time
        while current_time <= end_time:
            timestamp_str = current_time.strftime("%Y-%m-%dT%H:00:00")
            filled_data.append(
                {
                    "timestamp": timestamp_str,
                    value_key: existing_data.get(
                        timestamp_str, 0
                    ),  # Use existing value or default to 0
                }
            )
            current_time += step

        return filled_data

    async def handle_filters(self):
        query = ""
        params = []
        for filter_item in self.filters:
            filter_item.get("column_id")
            filter_config = filter_item.get("filter_config", {})
            filter_type = filter_config.get("filter_type")
            filter_op = filter_config.get("filter_op")
            filter_value = filter_config.get("filter_value")

            if (
                filter_type == "datetime"
                and filter_op == "between"
                and isinstance(filter_value, list)
                and len(filter_value) == 2
            ):
                query += " AND time_bucket BETWEEN %s AND %s"
                params.extend(filter_value)

        return query, params

    async def send_traffic_data(self):
        if self.interval not in ["hour", "day", "week", "month"]:
            raise ValueError("Invalid interval")

        # Define filters for the query
        filters = {"project_id": self.project_id}

        # Define the keys expected in the response
        keys = ["timestamp", "traffic"]

        # Fetch traffic data
        traffic_data = await self.fetch_data(self.interval, keys, filters)

        # Fill missing timestamps if necessary
        traffic_data = await self.generate_complete_timeline(traffic_data, "traffic")

        # Send data as JSON
        await self.send_json(
            {
                "project_id": self.project_id,
                "interval": self.interval,
                "system_metrics": {"traffic": traffic_data},
            }
        )

    async def send_latency_data(self):
        if self.interval not in ["hour", "day", "week", "month"]:
            raise ValueError("Invalid interval")

        # Define filters for the query
        filters = {"project_id": self.project_id}

        latency_data = await self.fetch_data(
            self.interval, ["timestamp", "latency"], filters
        )
        latency_data = await self.generate_complete_timeline(latency_data, "latency")

        await self.send_json(
            {
                "project_id": self.project_id,
                "interval": self.interval,
                "system_metrics": {"latency": latency_data},
            }
        )

    async def send_cost_data(self):
        if self.interval not in ["hour", "day", "week", "month"]:
            raise ValueError("Invalid interval")

        # Define filters for the query
        filters = {"project_id": self.project_id}

        cost_data = await self.fetch_data(self.interval, ["timestamp", "cost"], filters)
        cost_data = await self.generate_complete_timeline(cost_data, "cost")

        await self.send_json(
            {
                "project_id": self.project_id,
                "interval": self.interval,
                "system_metrics": {"cost": cost_data},
            }
        )

    async def send_tokens_data(self):
        if self.interval not in ["hour", "day", "week", "month"]:
            raise ValueError("Invalid interval")

        # Define filters for the query
        filters = {"project_id": self.project_id}

        tokens_data = await self.fetch_data(
            self.interval, ["timestamp", "tokens"], filters
        )
        tokens_data = await self.generate_complete_timeline(tokens_data, "tokens")

        await self.send_json(
            {
                "project_id": self.project_id,
                "interval": self.interval,
                "system_metrics": {"tokens": tokens_data},
            }
        )

    def _get_time_window(self):
        """Return (start_dt, end_dt) from filters, or a default window based on interval."""
        for f in self.filters:
            cfg = f.get("filterConfig", {})
            if (
                cfg.get("filterType") == "datetime"
                and cfg.get("filterOp") == "between"
                and isinstance(cfg.get("filterValue"), list)
                and len(cfg["filterValue"]) == 2
            ):
                try:
                    start = datetime.strptime(cfg["filterValue"][0], "%Y-%m-%dT%H:%M:%S.%fZ")
                    end = datetime.strptime(cfg["filterValue"][1], "%Y-%m-%dT%H:%M:%S.%fZ")
                    return start, end
                except (ValueError, TypeError):
                    pass
        # No explicit time filter — default window prevents full-table scans.
        now = datetime.utcnow()
        lookback = {
            "hour": timedelta(hours=24),
            "day": timedelta(days=30),
            "week": timedelta(weeks=12),
            "month": timedelta(days=365),
        }
        return now - lookback.get(self.interval, timedelta(days=30)), now

    async def send_evaluation_data(self):
        trunc_map = {
            "hour": "DATE_TRUNC('hour', os.created_at)",
            "day": "DATE_TRUNC('day', os.created_at)",
            "week": "DATE_TRUNC('week', os.created_at)",
            "month": "DATE_TRUNC('month', os.created_at)",
        }

        # Bound the CTE subqueries to the selected (or default) time window so
        # full-project scans don't OOM or timeout on large installations (#307).
        win_start, win_end = self._get_time_window()

        query = f"""
            WITH eval_configs AS (
            SELECT DISTINCT custom_eval_config_id
            FROM tracer_eval_logger
            WHERE observation_span_id IN (
                SELECT id
                FROM tracer_observation_span
                WHERE project_id = %s
                AND created_at BETWEEN %s AND %s
            )
        ),
        eval_metrics AS (
            SELECT
                observation_span_id,
                custom_eval_config_id,
                ROUND(cast(AVG(output_float) * 100 as numeric), 2) AS float_score,
                ROUND(AVG(
                    CASE
                        WHEN output_bool = TRUE THEN 100
                        WHEN output_bool = FALSE THEN 0
                        ELSE NULL
                    END
                ), 2) AS bool_score
            FROM tracer_eval_logger
            WHERE observation_span_id IN (
                SELECT id
                FROM tracer_observation_span
                WHERE project_id = %s
                AND created_at BETWEEN %s AND %s
            )
            AND (output_str IS NULL OR output_str != 'ERROR')
            GROUP BY observation_span_id, custom_eval_config_id
        ),
        distinct_str_values AS (
            SELECT DISTINCT jsonb_array_elements_text(output_str_list) AS value
            FROM tracer_eval_logger
            WHERE observation_span_id IN (
                SELECT id
                FROM tracer_observation_span
                WHERE project_id = %s
                AND created_at BETWEEN %s AND %s
            )
            AND output_str_list IS NOT NULL
        ),
        str_list_avg AS (
            SELECT
                el.observation_span_id,
                el.custom_eval_config_id,
                dsv.value,
                ROUND(AVG(
                    CASE
                        WHEN el.output_str_list @> jsonb_build_array(dsv.value) THEN 100
                        ELSE 0
                    END
                ), 2) AS avg_score
            FROM tracer_eval_logger el
            JOIN tracer_observation_span os ON el.observation_span_id = os.id
            JOIN distinct_str_values dsv ON el.output_str_list @> jsonb_build_array(dsv.value)
            WHERE os.project_id = %s
            AND os.created_at BETWEEN %s AND %s
            GROUP BY el.observation_span_id, el.custom_eval_config_id, dsv.value
        ),
        str_list_scores AS (
            SELECT
                observation_span_id,
                custom_eval_config_id,
                jsonb_object_agg(value, jsonb_build_object('score', avg_score)) AS str_list_score
            FROM str_list_avg
            GROUP BY observation_span_id, custom_eval_config_id
        )
        SELECT
            {trunc_map[self.interval]} as timestamp,
            coalesce(em.custom_eval_config_id, sls.custom_eval_config_id) as config_id,
            jsonb_object_agg(
                'metric_' || COALESCE(em.custom_eval_config_id, sls.custom_eval_config_id),
                COALESCE(
                    CASE
                        WHEN em.float_score IS NOT NULL THEN jsonb_build_object('score', em.float_score)
                        WHEN em.bool_score IS NOT NULL THEN jsonb_build_object('score', em.bool_score)
                        WHEN sls.str_list_score IS NOT NULL THEN sls.str_list_score
                        ELSE jsonb_build_object('score', 0.0)
                    END,
                    jsonb_build_object('score', 0.0)
                )
            ) AS metrics,
            tcec.name
            {",t.id AS id_trace" if self.graph == 'trace' else ""}
            {",os.id AS id_span" if self.graph == 'span' else ""}
        FROM tracer_observation_span os
        JOIN tracer_trace t ON os.trace_id = t.id
        LEFT JOIN eval_metrics em ON os.id = em.observation_span_id
        LEFT JOIN str_list_scores sls ON os.id = sls.observation_span_id
        JOIN tracer_custom_eval_config tcec ON COALESCE(em.custom_eval_config_id, sls.custom_eval_config_id) = tcec.id
        WHERE os.project_id = %s
        """

        query = FilterEngine.get_sql_filter_conditions_for_system_metrics(
            self.filters, query
        )
        query, having = FilterEngine.get_sql_filter_conditions_for_eval_metrics(
            self.filters, query
        )
        query += f"""
        GROUP BY timestamp, config_id, tcec.name{", id_trace" if self.graph == 'trace' else ''}{", id_span" if self.graph == 'span' else ""}{';' if not having else " "+having}"""

        params = (
            [self.project_id, win_start, win_end]  # eval_configs
            + [self.project_id, win_start, win_end]  # eval_metrics
            + [self.project_id, win_start, win_end]  # distinct_str_values
            + [self.project_id, win_start, win_end]  # str_list_avg
            + [self.project_id]  # main SELECT
        )
        rows = await self.fetch_raw_data(query, params)

        if self.eval_id:
            # Use parameterized query to prevent SQL injection.
            # The eval_id comes from the WebSocket message and must not be
            # interpolated directly into the SQL string.
            name_q = """SELECT tcec.name
                        FROM tracer_custom_eval_config tcec
                        WHERE tcec.id = %s;"""
            name = await self.fetch_raw_data(name_q, [self.eval_id])
            evaluations = {
                self.eval_id: {"name": "", "data": [{"name": name[0], "value": []}]}
            }
        else:
            evaluations = {}

        primary_traffic = None
        if hasattr(self, "graph") and self.graph in ["trace", "span"]:
            primary_traffic = await self.get_primary_data()

        for row in rows:
            if self.graph == "trace" or self.graph == "span":
                timestamp, config_id, metrics, name, _ = row
            else:
                timestamp, config_id, metrics, name = row
            config_id_str = str(config_id)

            if self.eval_id:
                if config_id_str != self.eval_id:
                    continue
                else:
                    evaluations[config_id_str] = {"name": name, "data": []}

            if config_id_str not in evaluations:
                evaluations[config_id_str] = {"name": name, "data": []}

            metric_score = json.loads(metrics).get(f"metric_{config_id}")

            if "score" in metric_score:
                if not evaluations[config_id_str]["data"]:
                    evaluations[config_id_str]["data"].append(
                        {"name": name, "value": []}
                    )

                value_entry = {
                    "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                    "value": metric_score["score"],
                }

                evaluations[config_id_str]["data"][0]["value"].append(value_entry)
            else:
                data_entries = []
                for choice_name, value in metric_score.items():
                    value_entry = {
                        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                        "value": value.get("score"),
                    }

                    data_entries.append({"name": choice_name, "value": [value_entry]})

                evaluations[config_id_str]["data"] = data_entries

        if self.property:
            for _config_id, eval_data in evaluations.items():
                for item in eval_data["data"]:
                    all_values = [
                        entry["value"]
                        for entry in item["value"]
                        if entry["value"] is not None
                    ]

                    total_count = max(len(all_values), 1)
                    empty_count = sum(1 for v in all_values if v == 0)

                    property_calculations = {
                        "count": lambda tc=total_count: tc,
                        "percentile empty": lambda ec=empty_count, tc=total_count: (
                            round((ec / tc * 100), 2) if tc > 0 else 0
                        ),
                        "sum": lambda av=all_values: (sum(av) if av else 0),
                        "average": lambda av=all_values, tc=total_count: (
                            round(sum(av) / tc, 2) if av else 0
                        ),
                        "standard deviation": lambda av=all_values: (
                            round(np.std(av), 2) if av else 0
                        ),
                        "p50": lambda av=all_values: (
                            round(np.median(av), 2) if av else 0
                        ),
                        "p75": lambda av=all_values: (
                            round(np.quantile(av, q=0.75), 2) if av else 0
                        ),
                        "p95": lambda av=all_values: (
                            round(np.quantile(av, q=0.95), 2) if av else 0
                        ),
                    }

                    computed_value = property_calculations.get(
                        self.property.lower(), lambda: 0
                    )()

                    for entry in item["value"]:
                        entry["value"] = computed_value

        for _config_id, eval_data in evaluations.items():
            for item in eval_data["data"]:
                item["value"] = await self.generate_complete_timeline(
                    item["value"], "value"
                )

        for _config_id, eval_data in evaluations.items():
            for item in eval_data["data"]:
                for entry in item["value"]:
                    value_entry_timestamp = entry["timestamp"]

                    if primary_traffic is not None:
                        matching_traffic = next(
                            (
                                t
                                for t in primary_traffic
                                if t["timestamp"] == value_entry_timestamp
                            ),
                            {"value": 0},
                        )
                        entry["primary_traffic"] = matching_traffic["value"]

        await self.send_json(
            {"type": "evaluations", "project_id": self.project_id, "data": evaluations}
        )

    async def get_primary_data(self):
        # Note: self.interval is validated against a whitelist in the callers
        # (send_traffic_data, etc.) so it is safe to interpolate here.
        # All user-supplied filter values use parameterized queries below.
        base_primary_query = f"""WITH truncated_eval_loggers AS (
                                    SELECT
                                        date_trunc('{self.interval}', tracer_eval_logger.created_at) AS interval,
                                        tracer_eval_logger.id
                                    FROM tracer_eval_logger
                                    JOIN tracer_trace ON tracer_eval_logger.trace_id = tracer_trace.id
                                    JOIN tracer_observation_span ON tracer_eval_logger.observation_span_id = tracer_observation_span.id
                                """
        condition = ""
        params = []
        trace_id = None

        created_at_condition = ""
        if self.filters:
            for filter in self.filters:
                if filter.get("columnId") == "trace_id":
                    filter_config = filter.get("filterConfig", {})
                    trace_id = filter_config.get("filterValue")
                    if trace_id:
                        try:
                            str(uuid.UUID(trace_id))
                        except ValueError:
                            continue
                        # Use parameterized query instead of string interpolation
                        condition = "WHERE tracer_eval_logger.trace_id = %s"
                        params.append(trace_id)
                elif filter.get("columnId") == "created_at":
                    filter_config = filter.get("filterConfig", {})
                    start_date = filter_config.get("startDate")
                    end_date = filter_config.get("endDate")
                    if start_date and end_date:
                        # Use parameterized query instead of string interpolation
                        created_at_condition = (
                            "AND tracer_eval_logger.created_at BETWEEN %s AND %s"
                        )
                        params.extend([start_date, end_date])
                    break
        primary_data_q = (
            base_primary_query
            + condition
            + " "
            + created_at_condition
            + ")"
            + """
                            SELECT interval,
                                COUNT(id) AS log_count
                            FROM truncated_eval_loggers
                            GROUP BY interval
                            ORDER BY interval;"""
        )
        primary_traffic = await self.fetch_raw_data(
            primary_data_q, params if params else None
        )

        formatted_traffic = []
        for row in primary_traffic:
            timestamp, log_count = row
            formatted_traffic.append(
                {
                    "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                    "value": log_count,
                }
            )

        return formatted_traffic

    @database_sync_to_async
    def fetch_data(self, interval, keys, filters):
        trunc_map = {
            "hour": "DATE_TRUNC('hour', created_at)",
            "day": "DATE_TRUNC('day', created_at)",
            "week": "DATE_TRUNC('week', created_at)",
            "month": "DATE_TRUNC('month', created_at)",
        }

        if interval not in trunc_map:
            raise ValueError("Invalid interval")

        # Map available columns to their respective names in the query
        column_map = {
            "timestamp": trunc_map[interval],
            "traffic": "COUNT(id)",
            "latency": "AVG(latency_ms)",
            "tokens": "SUM(total_tokens)",
            "cost": "SUM(prompt_tokens * 0.00000015 + completion_tokens * 0.0000006)",
        }

        # Select only the required columns
        selected_columns = [column_map[key] for key in keys if key in column_map]
        if not selected_columns:
            raise ValueError("Invalid keys provided")

        query = f"""
                SELECT {', '.join(selected_columns)}
                FROM tracer_observation_span
                WHERE project_id = %s
            """

        if keys[1] == "latency":
            query += """
            AND parent_span_id IS NULL"""

        query += f"""
            GROUP BY {column_map['timestamp']}
            ORDER BY {column_map['timestamp']};
        """
        params = [filters.get("project_id")]

        with connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        return [
            {
                keys[i]: (
                    row[i].strftime("%Y-%m-%dT%H:%M:%S")
                    if isinstance(row[i], datetime)
                    else float(row[i]) if isinstance(row[i], Decimal) else row[i]
                )
                for i in range(len(keys))
            }
            for row in rows
        ]

    @database_sync_to_async
    def fetch_raw_data(self, query, params=None):
        with connection.cursor() as cursor:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            rows = cursor.fetchall()
        return rows
