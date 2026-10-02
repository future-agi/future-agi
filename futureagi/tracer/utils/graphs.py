import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import structlog
from dateutil.relativedelta import relativedelta
from django.contrib.postgres.aggregates import ArrayAgg
from django.db import close_old_connections, connection, models
from django.db.models import F
from django.db.models.functions import TruncDay, TruncHour, TruncMonth

logger = structlog.get_logger(__name__)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tfc.utils.functions import calculate_eval_average
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan


class GraphEngine:
    def __init__(
        self,
        objects,
        interval,
        filters,
        property="average",
        observe_type=None,
        error=False,
        req_data_config=None,
    ):
        """
        Initialize graph engine for generating and formating data
        """
        self.objects = objects
        self.interval = interval
        self.filters = filters
        self.property = property
        self.timestamps, self.start_date, self.end_date = self._generate_timestamps()
        self.primary_traffic = self.get_primary_traffic(
            observe_type=observe_type, req_data_config=req_data_config
        )
        self.error = error

    def generate_graph(self, *args, **kwargs):
        """
        Generate and format graph data
        """
        if kwargs:
            metric = kwargs.get("metric") if "metric" in kwargs else None

        def safe_get_system_metrics(metric_config):
            try:
                close_old_connections()
                result = self.get_system_metrics(metric=metric_config)
                return result
            except Exception as e:
                logger.error(f"Error in get_system_metrics: {e}")
                raise
            finally:
                connection.close()
                close_old_connections()

        def safe_get_traffic():
            try:
                close_old_connections()
                result = self.get_traffic()
                return result
            except Exception as e:
                logger.error(f"Error in get_traffic: {e}")
                raise
            finally:
                connection.close()
                close_old_connections()

        if metric == "users_graph":
            self.objects = self._truncate_timestamp(self.objects)
            metrics = [
                {"display": "session", "name": "session_id"},
                {"display": "trace", "name": "trace_id"},
                {"display": "cost", "name": "cost"},
                {"display": "input_tokens", "name": "prompt_tokens"},
                {"display": "output_tokens", "name": "completion_tokens"},
            ]

            results = {}
            futures = []

            with ThreadPoolExecutor(max_workers=4) as executor:
                # Submit all metric tasks
                for metric_config in metrics:
                    future = executor.submit(safe_get_system_metrics, metric_config)
                    futures.append(future)

                # Collect results
                for future in as_completed(futures):
                    try:
                        ans = future.result()
                        if (
                            ans
                            and "metric_name" in ans
                            and ans.get("metric_name", None) is not None
                        ):
                            results[ans["metric_name"]] = ans["data"]
                    except Exception as e:
                        logger.error(f"Error in system_metrics task: {e}")

            return results

        if metric == "system_metrics":
            self.objects = self._truncate_timestamp(self.objects)
            # Define metrics configurations
            metrics = [
                {"display": "latency", "name": "latency_ms"},
                {"display": "tokens", "name": "total_tokens"},
                {"display": "cost", "name": "prompt_tokens,completion_tokens"},
            ]

            results = {}
            futures = []

            with ThreadPoolExecutor(max_workers=4) as executor:
                # Submit all metric tasks
                for metric_config in metrics:
                    future = executor.submit(safe_get_system_metrics, metric_config)
                    futures.append(future)

                # Submit traffic task
                traffic_future = executor.submit(safe_get_traffic)
                futures.append(traffic_future)

                # Collect results
                for future in as_completed(futures):
                    try:
                        ans = future.result()
                        if (
                            ans
                            and "metric_name" in ans
                            and ans.get("metric_name", None) is not None
                        ):
                            results[ans["metric_name"]] = ans["data"]
                    except Exception as e:
                        logger.error(f"Error in system_metrics task: {e}")

            return results

        if metric == "system_metric":
            self.objects = self._truncate_timestamp(self.objects)

            # Define metrics configurations
            metrics = {
                "latency": {"display": "latency", "name": "latency_ms"},
                "tokens": {"display": "tokens", "name": "total_tokens"},
                "cost": {"display": "cost", "name": "prompt_tokens,completion_tokens"},
            }

            metric_name = kwargs.get("metric_name", None)
            if not metric_name:
                raise Exception("Metric name not found in Graph Engine.")

            metric_data = self.get_system_metrics(metric=metrics[metric_name])

            return metric_data

        if metric == "monitor_system_metric":
            self.objects = self._truncate_timestamp(self.objects)
            mapping = {
                "latency": "latency_ms",
                "tokens": "total_tokens",
                "cost": "cost",
            }
            metric_name = kwargs.get("metric_name", None)
            if not metric_name:
                raise Exception("Metric name not found in Graph Engine.")
            metric_data = self.get_system_metrics(
                metric={"display": "value", "name": mapping[metric_name]}
            )
            return metric_data["data"]

        if metric == "evaluations":
            eval_data = self.get_eval_data()
            return eval_data

        if metric == "monitor_eval":
            eval_data = self.get_eval_data(metric="monitor_eval")
            return eval_data

        if metric == "observe_new":
            eval_data = self.observe_eval_data(
                metric="observe", req_eval=kwargs.get("req_data_config", None)
            )
            return eval_data

        if metric == "observe_annotation":
            annotation_data = self.observe_annotation_data(
                metric="observe_annotation",
                req_annotation=kwargs.get("req_data_config", None),
            )
            return annotation_data

        if metric == "eval_metric":
            eval_template = kwargs.get("eval_template", None)
            if not eval_template:
                raise Exception("Eval Template not found in Graph Engine.")

            self.objects = self._truncate_timestamp(self.objects)

            results = {}
            future_list = []

            def fetch_metric_with_connection(eval_template):
                close_old_connections()
                result = self.get_eval_metric_data(eval_template=eval_template)
                connection.close()
                close_old_connections()
                return "eval_results", result

            def fetch_error_with_connection(eval_template):
                close_old_connections()
                result = self.get_error_graph_data(eval_template=eval_template)
                connection.close()
                close_old_connections()
                return "error_rate", result

            with ThreadPoolExecutor(max_workers=2) as executor:
                eval_future = executor.submit(
                    fetch_metric_with_connection, eval_template
                )
                future_list.append(eval_future)

                if self.error:
                    error_future = executor.submit(
                        fetch_error_with_connection, eval_template
                    )
                    future_list.append(error_future)

                for future in as_completed(future_list):
                    try:
                        name, data = future.result()
                        results[name] = data
                    except Exception as e:
                        logger.error(f"Error fetching {name}: {e}")

            return {
                "count_graph_data": results.get("eval_results", {}).get(
                    "count_graph_data", None
                ),
                "avg_graph_data": results.get("eval_results", {}).get(
                    "avg_graph_data", None
                ),
                "error_rate": results.get("error_rate", None),
            }

    def _generate_timestamps(self):
        """
        Generate timestamps for the given interval.

        - `filters` have the start date and end date
        - `interval` can be "hour", "day", "week", or "month".

        Returns a list of timestamps in "%Y-%m-%dT%H:%M:%S.%f%Z format.
        """

        dates = []
        timestamps = []
        current_date = None

        start_date = None
        end_date = None
        for filter in self.filters:
            filter_config = filter.get("filter_config")
            if filter_config.get("filter_type") == "datetime":
                filter_value = filter_config.get("filter_value")
                if isinstance(filter_value, list):
                    start_date = datetime.strptime(
                        filter_value[0], "%Y-%m-%dT%H:%M:%S.%fZ"
                    )
                    end_date = datetime.strptime(
                        filter_value[1], "%Y-%m-%dT%H:%M:%S.%fZ"
                    )
                elif isinstance(filter_value, str):
                    dates.append(
                        datetime.strptime(filter_value, "%Y-%m-%dT%H:%M:%S.%fZ")
                    )

        if len(dates) > 1:
            if not start_date:
                start_date = min(dates)

            if not end_date:
                end_date = max(dates)
        else:
            if not start_date:
                start_date = min(dates) if dates else datetime.now() - timedelta(days=7)
            if not end_date:
                end_date = datetime.now()

        if self.interval == "month":
            start_date = start_date.replace(
                day=start_date.day, hour=0, minute=0, second=0, microsecond=0
            )
        elif self.interval == "hour":
            start_date = start_date.replace(minute=0, second=0, microsecond=0)
        else:
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

        current_date = start_date

        i = 0

        while start_date <= end_date:
            timestamps.append({"start": start_date})
            if self.interval == "hour":
                start_date += timedelta(hours=1)
                timestamps[i].update({"end": start_date})
            elif self.interval == "day":
                start_date += timedelta(days=1)
                timestamps[i].update({"end": start_date})
            elif self.interval == "week":
                start_date += timedelta(weeks=1)
                timestamps[i].update({"end": start_date})
            elif self.interval == "month":
                start_date += relativedelta(months=1)
                timestamps[i].update({"end": start_date})
            else:
                raise Exception("Invalid interval specified in Graph Engine.")
            i += 1

        return timestamps, current_date, end_date

    def _truncate_timestamp(self, objects=None):
        """
        Truncate timestamps for grouping

        CH25-TODO(generic-helper-needs-callers-migrated): operates on a
        Django queryset (self.objects, passed by the caller) and annotates
        it with TruncHour/Day/Month("created_at"). The only live direct
        caller of GraphEngine today is
        model_hub/views/separate_evals.py:391 (eval-metric data path) —
        tracer/views/* import the newer graphs_optimized module instead.
        Migrating GraphEngine requires migrating that caller first;
        CHSpanReader.time_bucket_aggregate is the right primitive for
        the ObservationSpan-only branches once the caller switches from
        "build queryset, hand to GraphEngine" to "ask CH for buckets
        directly" (the pattern graphs_optimized.py uses today).
        """
        if not objects or len(objects) == 0:
            objects = ObservationSpan.objects.filter(
                id__in=[obj.id for obj in self.objects]
            )
        try:
            if self.interval == "hour":
                objects = objects.annotate(interval=TruncHour("created_at"))
            elif self.interval == "day":
                objects = objects.annotate(interval=TruncDay("created_at"))
            elif self.interval == "week":
                objects = objects.annotate(interval=TruncDay("created_at"))
            elif self.interval == "month":
                objects = objects.annotate(interval=TruncMonth("created_at"))
            else:
                raise Exception("Invalid interval specified in Graph Engine.")
            return objects

        except Exception as e:
            traceback.print_exc()
            raise e

    def get_system_metrics(self, metric: dict):
        count = [
            {
                "timestamp": ts["start"],
                metric["display"]: 0,
                "value": 0,
                "primary_traffic": 0,
            }
            for ts in self.timestamps
        ]
        if metric["display"] == "session":
            self.objects = self.objects.annotate(session_id=F("trace__session_id"))
        req_objects = list(self.objects)
        filtered_objects = []

        for i in range(len(count)):

            if req_objects is not None and len(req_objects) > 0:
                filtered_objects = [
                    req_object
                    for req_object in req_objects
                    if req_object.interval.replace(tzinfo=None)
                    >= self.timestamps[i]["start"]
                    and req_object.interval.replace(tzinfo=None)
                    < self.timestamps[i]["end"]
                ]

            if metric["display"] == "session":
                # Collect distinct session IDs
                session_ids = {
                    obj.session_id
                    for obj in filtered_objects
                    if hasattr(obj, "session_id") and obj.session_id is not None
                }

                # Store count of distinct sessions
                session_count = len(session_ids)
                count[i]["session"] = session_count
                count[i]["value"] = session_count

            elif metric["display"] == "trace":
                # Collect distinct trace IDs
                trace_ids = {
                    obj.trace_id
                    for obj in filtered_objects
                    if hasattr(obj, "trace_id") and obj.trace_id is not None
                }

                # Store count of distinct traces
                trace_count = len(trace_ids)
                count[i]["trace"] = trace_count
                count[i]["value"] = trace_count

            # elif metric["display"] == "cost":
            #     total_cost = 0
            #     for obj in filtered_objects:
            #        total_cost = total_cost + obj.cost
            #     count[i]["cost"] = int(total_cost)
            #     count[i]["value"] = int(total_cost)
            #     count[i]["primary_traffic"] = len(filtered_objects)

            else:
                objects = [getattr(obj, metric["name"], 0) for obj in filtered_objects]
                count[i][metric["display"]] += sum(
                    [0 if obj is None else obj for obj in objects]
                )

                count[i]["value"] = (
                    round(count[i][metric["display"]], 6)
                    if metric["display"] == "cost"
                    else count[i][metric["display"]]
                )
                count[i][metric["display"]] = (
                    round(count[i][metric["display"]], 6)
                    if metric["display"] == "cost"
                    else count[i][metric["display"]]
                )
                count[i]["primary_traffic"] = len(objects)

        return {"metric_name": metric["display"], "data": count}

    def get_traffic(self):
        traffic_data = [{"timestamp": ts, "traffic": 0} for ts in self.timestamps]
        for timestamp, logs in self.primary_traffic.items():
            for data_point in traffic_data:
                if (
                    data_point["timestamp"]["start"] < timestamp.replace(tzinfo=None)
                    and timestamp.replace(tzinfo=None) <= data_point["timestamp"]["end"]
                ):
                    data_point["traffic"] += len(logs)
                    break
        for tf_data in traffic_data:
            tf_data["timestamp"] = tf_data["timestamp"]["start"]
        return {"metric_name": "traffic", "data": traffic_data}

    def get_eval_data(self, metric=None):
        """
        Get evaluation data with optimized database queries and processing
        """
        try:
            # Initial query optimization with select_related
            base_query = EvalLogger.objects.select_related(
                "custom_eval_config", "trace"
            )

            if not metric:
                loggers = base_query.filter(
                    observation_span_id__in=[span.id for span in self.objects]
                )
                loggers = self._truncate_timestamp(loggers)
            else:
                loggers = self._truncate_timestamp(self.objects)

            # Fetch all custom eval configs in one query
            custom_eval_config_ids = list(
                set(
                    filter(
                        None, loggers.values_list("custom_eval_config_id", flat=True)
                    )
                )
            )
            config_map = {
                str(config.id): config.name
                for config in CustomEvalConfig.objects.filter(
                    id__in=custom_eval_config_ids, deleted=False
                )
            }

            result = {}

            # Handle empty loggers case
            if not loggers and custom_eval_config_ids:
                return {
                    str(config_id): {
                        "name": config_map.get(str(config_id), "Unknown"),
                        "data": [
                            {"timestamp": ts["start"], "value": 0}
                            for ts in self.timestamps
                        ],
                    }
                    for config_id in custom_eval_config_ids
                }

            if custom_eval_config_ids:
                # Process each config in parallel
                def process_result_with_connection(
                    config_id, loggers, metric, config_map
                ):
                    close_old_connections()
                    result = self._process_eval_config(
                        config_id=config_id,
                        loggers=loggers,
                        metric=metric,
                        config_map=config_map,
                    )
                    connection.close()
                    close_old_connections()
                    return result

                with ThreadPoolExecutor(
                    max_workers=min(len(custom_eval_config_ids), 4)
                ) as executor:
                    future_to_config = {
                        executor.submit(
                            process_result_with_connection,
                            config_id,
                            loggers,
                            metric,
                            config_map,
                        ): config_id
                        for config_id in custom_eval_config_ids
                    }

                    for future in future_to_config:
                        config_id = future_to_config[future]
                        try:
                            config_result = future.result()
                            if config_result:
                                result[str(config_id)] = config_result
                        except Exception:
                            traceback.print_exc()

            return result

        except Exception as e:
            traceback.print_exc()
            raise e

    def _process_eval_config(self, config_id, loggers, metric, config_map):
        """
        Helper method to process individual eval configs
        """
        eval_objects = loggers.filter(custom_eval_config_id=config_id)

        # Prepare data structure
        data = [
            {"timestamp": ts, "value": 0, **({"primary_traffic": 0} if metric else {})}
            for ts in self.timestamps
        ]

        # Group logs by interval
        interval_logs = defaultdict(list)
        for log in eval_objects:
            interval_logs[log.interval].append(log)

        # Process each interval
        for interval, logs in interval_logs.items():
            eval_response_data = self._score_evals_property(logs)

            # Update matching timestamp data
            for data_point in data:
                if data_point["timestamp"]["start"] <= interval.replace(
                    tzinfo=None
                ) and data_point["timestamp"]["end"] > interval.replace(tzinfo=None):
                    data_point["value"] = eval_response_data.get("value", 0)

        # Format final data
        formatted_data = [
            {
                "timestamp": d["timestamp"]["start"],
                "value": d["value"],
            }
            for d in data
        ]

        return {
            "name": config_map.get(str(config_id), "Unknown"),
            "data": formatted_data,
        }

    def _score_evals_property(self, evals):
        """
        Optimized method to calculate evaluation scores
        """
        if not evals:
            return {"value": 0, "eval_response_data": {}}

        total_count = len(evals)
        valid_scores = []
        eval_response_data = {}
        empty_count = 0

        # Process all evals in a single pass
        for eval_log in evals:
            eval_id = eval_log.custom_eval_config.id

            # Initialize eval_response_data if needed
            if eval_id not in eval_response_data:
                eval_response_data[eval_id] = {
                    "passed_count": 0,
                    "failed_count": 0,
                    "count": 0,
                    "failed_traces_count": 0,
                    "failed_traces_ids": set(),  # Using set for faster lookups
                    "name": f"Low {eval_log.custom_eval_config.name}",
                }

            current_data = eval_response_data[eval_id]
            current_data["count"] += 1

            # Check for empty outputs
            if all(
                getattr(eval_log, field) in (None, "", [])
                for field in [
                    "output_bool",
                    "output_float",
                    "output_str",
                    "output_str_list",
                ]
            ):
                empty_count += 1
                continue

            # Process different output types
            if eval_log.output_bool is not None:
                self._process_boolean_output(eval_log, current_data, valid_scores)
            elif eval_log.output_float is not None:
                self._process_float_output(eval_log, current_data, valid_scores)
            elif eval_log.output_str:
                self._process_string_output(eval_log, current_data, valid_scores)

        # Convert sets back to lists for JSON serialization
        for data in eval_response_data.values():
            data["failed_traces_ids"] = list(data["failed_traces_ids"])

        return self._calculate_final_score(
            valid_scores, total_count, empty_count, eval_response_data
        )

    def _process_boolean_output(self, eval_log, current_data, valid_scores):
        """Helper method for processing boolean outputs"""
        if eval_log.output_bool:
            valid_scores.append(100)
            current_data["passed_count"] += 1
        else:
            valid_scores.append(0)
            current_data["failed_count"] += 1
            if eval_log.trace.id not in current_data["failed_traces_ids"]:
                current_data["failed_traces_count"] += 1
                current_data["failed_traces_ids"].add(eval_log.trace.id)

    def _process_float_output(self, eval_log, current_data, valid_scores):
        """Helper method for processing float outputs"""
        score = min(max(eval_log.output_float * 100, 0), 100)
        valid_scores.append(score)
        if score >= 30:
            current_data["passed_count"] += 1
        else:
            current_data["failed_count"] += 1
            if eval_log.trace.id not in current_data["failed_traces_ids"]:
                current_data["failed_traces_count"] += 1
                current_data["failed_traces_ids"].add(eval_log.trace.id)

    def _process_string_output(self, eval_log, current_data, valid_scores):
        """Helper method for processing string outputs"""
        output_lower = eval_log.output_str.lower()
        if output_lower == "passed":
            valid_scores.append(100)
            current_data["passed_count"] += 1
        elif output_lower in ["failed", "error"]:
            valid_scores.append(0)
            current_data["failed_count"] += 1
            if eval_log.trace.id not in current_data["failed_traces_ids"]:
                current_data["failed_traces_count"] += 1
                current_data["failed_traces_ids"].add(eval_log.trace.id)
        else:
            valid_scores.append(100)
            current_data["passed_count"] += 1

    def _calculate_final_score(
        self, valid_scores, total_count, empty_count, eval_response_data
    ):
        """Helper method to calculate final scores based on property type"""
        if not valid_scores:
            return {"value": 0, "eval_response_data": eval_response_data}

        try:
            import numpy as np

            property_calculations = {
                "count": lambda: total_count,
                "percentile_empty": lambda: (
                    (empty_count / total_count * 100) if total_count > 0 else 0
                ),
                "average": lambda: round(sum(valid_scores) / total_count, 2),
                "standard_deviation": lambda: np.std(valid_scores),
                "p50": lambda: np.median(valid_scores),
                "p75": lambda: np.quantile(valid_scores, q=0.75),
                "p95": lambda: np.quantile(valid_scores, q=0.95),
            }

            calculation = property_calculations.get(self.property.lower())
            if calculation:
                return {
                    "value": calculation(),
                    "eval_response_data": eval_response_data,
                }

        except Exception:
            traceback.print_exc()

        return {"value": 0, "eval_response_data": eval_response_data}

    def get_primary_traffic(self, observe_type=None, req_data_config=None):
        """
        Get primary traffic data grouped by interval
        """
        try:
            if not req_data_config:
                return {}

            type = req_data_config.get("type", "SPAN")

            if type == "EVAL":
                base_query = EvalLogger.objects.select_related(
                    "trace", "observation_span"
                )
            elif type == "ANNOTATION":
                base_query = Score.objects.filter(deleted=False).select_related(
                    "observation_span"
                )
            # CH25-TODO(generic-helper-needs-callers-migrated): SPAN and
            # SYSTEM_METRIC both materialize an ObservationSpan queryset
            # that downstream `.annotate(ArrayAgg("id"))` aggregation
            # depends on (line ~780 below). Same blocker as
            # _truncate_timestamp — migrate callers to ask CH for the
            # bucket aggregate directly instead of constructing the
            # queryset here.
            elif type == "SPAN":
                base_query = ObservationSpan.objects.select_related("trace")
            elif type == "SYSTEM_METRIC":
                base_query = ObservationSpan.objects.select_related("trace")
            else:
                raise Exception("Invalid type specified in req_data_config")

            # Determine filter based on observe_type
            if observe_type == "trace":
                if type == "EVAL":
                    trace_ids = [obj.trace_id for obj in self.objects]
                    loggers = base_query.filter(trace_id__in=trace_ids)
                elif type == "ANNOTATION":
                    span_ids = [obj.observation_span_id for obj in self.objects]
                    loggers = base_query.filter(observation_span_id__in=span_ids)
            elif observe_type == "span":
                if type == "SYSTEM_METRIC":
                    span_ids = [obj.id for obj in self.objects]
                    loggers = base_query.filter(id__in=span_ids)
                else:
                    span_ids = [obj.observation_span_id for obj in self.objects]
                    loggers = base_query.filter(observation_span_id__in=span_ids)
            elif observe_type == "system_metric":
                loggers = base_query.filter(id__in=[span.id for span in self.objects])
            else:
                raise Exception("Invalid observe type")

            # Apply timestamp truncation and group by interval
            loggers = self._truncate_timestamp(loggers)
            # Group by interval and collect IDs
            interval_logs = (
                loggers.values("interval")
                .annotate(log_count=models.Count("id"), log_ids=ArrayAgg("id"))
                .order_by("interval")
            )

            # Convert to defaultdict format
            result = defaultdict(list)
            for log in interval_logs:
                result[log["interval"]] = log["log_ids"]

            return result

        except Exception as e:
            traceback.print_exc()
            raise e

    def get_eval_metric_data(self, eval_template):
        count_data = [{"timestamp": ts, "value": 0} for ts in self.timestamps]
        avg_data = [{"timestamp": ts, "value": 0} for ts in self.timestamps]

        try:
            # print("self.objects", self.objects)
            for i in range(len(count_data)):
                objs = self.objects.filter(
                    interval__gte=self.timestamps[i]["start"],
                    interval__lt=self.timestamps[i]["end"],
                )
                count = len(objs)
                average = calculate_eval_average(
                    eval_template=eval_template, api_logs=objs
                )

                # print("checking timestamped data",avg_data[i]['timestamp']['start'], avg_data[i]['value'], average)
                count_data[i]["value"] = count
                avg_data[i]["value"] = average
                count_data[i]["timestamp"] = count_data[i]["timestamp"]["start"]
                avg_data[i]["timestamp"] = avg_data[i]["timestamp"]["start"]

            return {"count_graph_data": count_data, "avg_graph_data": avg_data}
        except Exception as e:
            traceback.print_exc()
            raise e

    def get_error_graph_data(self, eval_template):
        error_data = [{"timestamp": ts, "value": 0} for ts in self.timestamps]

        try:
            for i in range(len(error_data)):
                objs = self.objects.filter(
                    interval__gte=self.timestamps[i]["start"],
                    interval__lt=self.timestamps[i]["end"],
                    status__in=["error", "insufficient_credits", "rate_limited"],
                )
                error_data[i]["value"] = objs.count() if objs is not None else 0
                error_data[i]["timestamp"] = error_data[i]["timestamp"]["start"]
            return error_data
        except Exception as e:
            traceback.print_exc()
            raise e

    def observe_eval_data(self, metric=None, req_eval=None):
        """
        Get evaluation data with optimized database queries and processing
        """
        try:
            loggers = self._truncate_timestamp(self.objects)

            if not loggers or not req_eval:
                return {
                    "name": "Unknown",
                    "data": [
                        {"timestamp": ts["start"], "value": 0} for ts in self.timestamps
                    ],
                }

            custom_eval_config_id = req_eval.get("id", None)
            if not custom_eval_config_id:
                custom_eval_config_id = loggers[0].custom_eval_config_id

            try:
                custom_eval_config = CustomEvalConfig.objects.get(
                    id=custom_eval_config_id
                )
            except CustomEvalConfig.DoesNotExist:
                return {
                    "name": "Unknown",
                    "data": [
                        {"timestamp": ts["start"], "value": 0} for ts in self.timestamps
                    ],
                }

            result = self.process_config(
                loggers=loggers, metric=metric, req_data_config=req_eval
            )

            choices = custom_eval_config.eval_template.choices
            name = custom_eval_config.name

            if choices and len(choices) > 0:
                name = name + " - " + str(req_eval.get("value", "Unknown"))

            return {
                "name": name or "Unknown",
                "data": result,
                "id": custom_eval_config_id,
            }

        except Exception as e:
            traceback.print_exc()
            raise e

    def process_config(self, loggers, metric, req_data_config=None):
        """
        Helper method to process individual eval configs
        """

        logger_objects = loggers
        # Prepare data structure
        data = [
            {"timestamp": ts, "value": 0, **({"primary_traffic": 0} if metric else {})}
            for ts in self.timestamps
        ]

        # Group logs by interval
        interval_logs = defaultdict(list)
        for log in logger_objects:
            interval_logs[log.interval].append(log)

        # Process each interval
        for interval, logs in interval_logs.items():
            avg_score = self.calculate_final_score(
                logs, req_data_config=req_data_config
            )

            # Update matching timestamp data
            for data_point in data:
                if data_point["timestamp"]["start"] <= interval.replace(
                    tzinfo=None
                ) and data_point["timestamp"]["end"] > interval.replace(tzinfo=None):
                    data_point["value"] = avg_score
                    if metric:
                        data_point["primary_traffic"] = (
                            len(self.primary_traffic.get(interval, []))
                            if self.primary_traffic
                            else 0
                        )

        # Format final data
        formatted_data = [
            {
                "timestamp": d["timestamp"]["start"],
                "value": d["value"],
                **({"primary_traffic": d["primary_traffic"]} if metric else {}),
            }
            for d in data
        ]
        return formatted_data

    def calculate_final_score(self, loggers, req_data_config=None):
        """
        Optimized method to calculate evaluation scores
        """
        if not loggers:
            return 0

        output_type = req_data_config.get("output_type", "float")

        if output_type == "bool":
            score = self.process_boolean_output(
                loggers, req_data_config=req_data_config
            )
            return score

        elif output_type == "str_list":
            return self.process_str_list_output(
                loggers, req_data_config=req_data_config
            )

        elif output_type == "float":
            return self.process_float_output(loggers, req_data_config=req_data_config)
        else:
            raise Exception("Invalid output type specified in req_data_config")

    def process_boolean_output(self, loggers, req_data_config=None):
        """Helper method for processing boolean outputs"""
        value = req_data_config.get("value", None)
        if not isinstance(value, bool):
            if value not in ["true", "false"]:
                raise Exception("Value must be a boolean or 'true' or 'false'")
            value = value == "true"

        type = req_data_config.get("type", None)

        if type == "EVAL":
            matching_loggers = [log for log in loggers if log.output_bool is not None]
            total_score = sum(
                100 if log.output_bool == value else 0 for log in matching_loggers
            )
        elif type == "ANNOTATION":
            # Score stores thumbs as {"value": "up"/"down"}
            thumbs_str = "up" if value else "down"
            matching_loggers = [
                log
                for log in loggers
                if isinstance(log.value, dict) and log.value.get("value") is not None
            ]
            total_score = sum(
                100 if log.value.get("value") == thumbs_str else 0
                for log in matching_loggers
            )
        else:
            raise Exception("Invalid type specified in req_data_config")

        if not matching_loggers:
            return 0

        return round(total_score / len(matching_loggers), 2)

    def process_str_list_output(self, loggers, req_data_config=None):
        """Helper method for processing string list outputs"""
        value = req_data_config.get("value", None)
        if not value:
            raise Exception("Value is required for str_list output type")

        type = req_data_config.get("type", None)

        if type == "EVAL":
            matching_loggers = [
                log
                for log in loggers
                if log.output_str_list and value in log.output_str_list
            ]
        elif type == "ANNOTATION":
            # Score stores categorical as {"selected": ["choice1", ...]}
            matching_loggers = [
                log
                for log in loggers
                if isinstance(log.value, dict)
                and isinstance(log.value.get("selected"), list)
                and value in log.value["selected"]
            ]
        else:
            raise Exception("Invalid type specified in req_data_config")

        total_loggers = len(loggers)
        if total_loggers == 0:
            return 0

        # Calculate percentage of matching loggers
        return round((len(matching_loggers) / total_loggers) * 100, 2)

    @staticmethod
    def _extract_score_float(score):
        """Extract the numeric value from a Score's JSON value dict.

        NUMERIC labels store ``{"value": float}``, STAR labels store
        ``{"rating": float}``.  Returns ``None`` when neither key is present.
        """
        if not isinstance(score.value, dict):
            return None
        val = (
            score.value.get("rating")
            if "rating" in score.value
            else score.value.get("value")
        )
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def process_float_output(self, loggers, req_data_config=None):
        """Helper method for processing float outputs"""
        type = req_data_config.get("type", None)

        if type == "EVAL":
            valid_loggers = [log for log in loggers if log.output_float is not None]
            total = sum(log.output_float for log in valid_loggers)
        elif type == "ANNOTATION":
            # Score stores NUMERIC as {"value": float} and STAR as {"rating": float}
            pairs = [(log, self._extract_score_float(log)) for log in loggers]
            valid_loggers = [log for log, v in pairs if v is not None]
            total = sum(v for _, v in pairs if v is not None)
        else:
            raise Exception("Invalid type specified in req_data_config")

        if not valid_loggers:
            return 0

        if type == "ANNOTATION":
            return round((total / len(valid_loggers)), 2)

        # Calculate average of float outputs and multiply by 100
        return round((total / len(valid_loggers)) * 100, 2)

    def observe_annotation_data(self, metric=None, req_annotation=None):
        """
        Get annotation data with optimized database queries and processing
        """
        try:
            loggers = self._truncate_timestamp(self.objects)

            if not loggers or not req_annotation:
                return {
                    "name": "Unknown",
                    "data": [
                        {"timestamp": ts["start"], "value": 0} for ts in self.timestamps
                    ],
                }

            annotation_label_id = req_annotation.get("id", None)
            if not annotation_label_id:
                annotation_label_id = loggers[0].label_id

            try:
                annotation_label = AnnotationsLabels.objects.get(id=annotation_label_id)
            except AnnotationsLabels.DoesNotExist:
                return {
                    "name": "Unknown",
                    "data": [
                        {"timestamp": ts["start"], "value": 0} for ts in self.timestamps
                    ],
                }

            result = self.process_config(
                loggers=loggers, metric=metric, req_data_config=req_annotation
            )

            return {"name": annotation_label.name or "Unknown", "data": result}

        except Exception as e:
            traceback.print_exc()
            raise e
