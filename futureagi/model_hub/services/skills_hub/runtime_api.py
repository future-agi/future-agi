"""Shared usage/comparison projections over bounded, authoritative runtime facts.

A request owns one CH deadline. The fact sentinel fails closed rather than
publishing sampled counts. Cohorts are sets of (project, trace), never sums of
version/agent aggregates. Postgres supplies every navigable catalogue identity.
"""

import json
from collections import Counter, defaultdict
from datetime import UTC, timedelta
from math import isfinite

from django.utils import timezone

from model_hub.models.skills_hub import HubSkillUsageDaily, HubSkillVersion
from model_hub.models.skills_hub_v12 import HubSkillContextDependency
from model_hub.selectors.skills_hub_v12 import get_resource, resources_for_scope
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_reads import (
    CATALOG_LIMIT,
    cursor_after,
    next_cursor,
    projects_in_scope,
    query_identity,
)
from model_hub.services.skills_hub.runtime_usage import (
    MIN_EVALUATED,
    MIN_TRACES,
    TOTALS_NOTE,
    ProjectScope,
    adoption_state,
    agent_identity,
    closure_diff,
    move_windows,
    read_runtime_statement,
    regression_verdict,
    runtime_diagnosis,
)
from model_hub.services.skills_hub.usage import can_record
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    RuntimeReadSession,
    SkillRuntimeQueryBuilder,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

FACT_LIMIT = 20000


def utc_row(row):
    # Both supported CH clients may return naive values for a UTC DateTime64.
    # Make that storage timezone explicit before comparing to HTTP windows.
    value = row.get("start_time")
    if value is not None and timezone.is_naive(value):
        return {**row, "start_time": value.replace(tzinfo=UTC)}
    return row


def trace_key(row):
    return str(row["project_id"]), row["trace_id"]


def agent_key(row):
    return str(row["project_id"]), row["service_name"], row["agent_name"]


def quantile(values, fraction):
    values = sorted(values)
    return values[min(int(len(values) * fraction), len(values) - 1)] if values else None


def mean(values):
    return sum(values) / len(values) if values else None


def number(value):
    value = float(value or 0)
    return value if isfinite(value) else 0.0


class RuntimeFacts:
    def __init__(self, *, principal, workspace, query, session=None):
        self.query, self.principal = query, principal
        self.resource = get_resource(principal=principal, resource_id=query["resource_id"], kind=query["kind"]) if query.get("resource_id") else None
        all_projects = {str(row.pk): row.name for row in projects_in_scope(principal=principal, workspace=workspace)}
        requested = query.get("project_ids")
        if query.get("project_id"):
            if requested is not None and str(query["project_id"]) not in requested:
                raise SkillsHubError("invalid_runtime_query", "The agent project must be selected.", status=400)
            requested = [str(query["project_id"])]
        scope = ProjectScope.select(workspace_ids=all_projects, readable_ids=all_projects, requested_ids=requested)
        self.projects = {key: all_projects[key] for key in scope.project_ids}
        self.truncated = scope.truncated
        skills = resources_for_scope(scope=principal.scope, kind="skill")
        if self.resource is not None and self.resource.kind == "skill":
            skills = skills.filter(pk=self.resource.pk)
        if query.get("_context_comparison"):
            skills = skills.none()
        if query.get("skill_ids"):
            skills = skills.filter(pk__in=query["skill_ids"])
            if skills.count() != len(query["skill_ids"]):
                raise SkillsHubError("runtime_resource_not_found", "A skill cannot be opened.", status=404)
        self.selected_skills = {str(row.pk): row for row in skills[:CATALOG_LIMIT + 1]}
        if len(self.selected_skills) > CATALOG_LIMIT:
            raise SkillsHubError("runtime_read_too_large", "Choose skills to narrow this read.", status=503)
        self.context_version = None
        if self.resource and self.resource.kind == "agent_context":
            self.context_version = self.resource.versions.filter(pk=query["context_version_id"]).first() if query.get("context_version_id") else self.resource.versions.order_by("-number").first()
            if query.get("context_version_id") and self.context_version is None:
                raise SkillsHubError("runtime_resource_not_found", "This context version cannot be opened.", status=404)
        versions = list(HubSkillVersion.objects.filter(skill__in=skills).select_related("skill").prefetch_related("labels").order_by("id")[:CATALOG_LIMIT + 1])
        if len(versions) > CATALOG_LIMIT:
            raise SkillsHubError("runtime_read_too_large", "Choose a skill to narrow this read.", status=503)
        self.versions = {str(row.pk): row for row in versions}
        if query.get("version_ids") and not set(query["version_ids"]).issubset(self.versions):
            raise SkillsHubError("invalid_runtime_query", "A version is not in this resource scope.", status=400)
        self.skills = self.selected_skills
        self.contexts = {str(row.pk): row for row in resources_for_scope(scope=principal.scope, kind="agent_context")[:CATALOG_LIMIT + 1]}
        if len(self.contexts) > CATALOG_LIMIT:
            raise SkillsHubError("runtime_read_too_large", "Too many contexts for this read.", status=503)
        self.session = session or RuntimeReadSession(V2AnalyticsQueryService(), deadline=ReadDeadline.start(3000))
        self.loads, self.failed, self.spans, self.evals, self.mismatches = [], [], [], [], []
        self.configs = {}
        self.spans_by_trace = defaultdict(list)
        self.metrics_cache = {}

    def read(self, start, end):
        self.start, self.end = start, end
        if not self.projects or start == end:
            return self
        builder = SkillRuntimeQueryBuilder(project_ids=self.projects, start=start, end=end)
        identities = [(str(v.skill_id), str(v.pk), v.digest) for v in self.versions.values()
                      if not self.query.get("version_ids") or str(v.pk) in self.query["version_ids"]]
        agent = (str(self.query["project_id"]), self.query.get("service", ""), self.query.get("agent", "")) if "service" in self.query or "agent" in self.query else None
        options = {"identities": identities, "skill_names": [s.name for s in self.skills.values()], "label": self.query.get("label"),
                   "context_id": self.resource.pk if self.resource is not None and self.resource.kind == "agent_context" else None, "agent": agent}
        if not self.query.get("_context_comparison"):
            self.loads = read_runtime_statement(self.session, builder.build_usage_loads(**options), max_rows=FACT_LIMIT)
            self.failed = read_runtime_statement(self.session, builder.build_usage_loads(**options, failed=True), max_rows=FACT_LIMIT)
            self.mismatches = read_runtime_statement(self.session, builder.build_usage_loads(**options, mismatch=True), max_rows=FACT_LIMIT)
        if self.query.get("context_version_id"):
            self.mismatches = [row for row in self.mismatches if str(row["context_version_id"]) == str(self.query["context_version_id"])]
            self.loads = [row for row in self.loads if str(row["context_version_id"]) == str(self.query["context_version_id"])]
            self.failed = [row for row in self.failed if str(row["context_version_id"]) == str(self.query["context_version_id"])]
        if self.query.get("_context_comparison"):
            context_versions = self.resource.versions.all()
            self.loads = read_runtime_statement(self.session, builder.build_context_members(resource_id=self.resource.pk,
                                                identities=[(str(self.resource.pk), str(row.pk), row.digest) for row in context_versions]), max_rows=FACT_LIMIT)
        self.loads = [utc_row(row) for row in self.loads]
        self.failed = [utc_row(row) for row in self.failed]
        self.mismatches = [utc_row(row) for row in self.mismatches]
        members = sorted({trace_key(row) for row in self.loads})
        if members:
            self.spans = [utc_row(row) for row in read_runtime_statement(self.session, builder.build_usage_spans(members=members), max_rows=FACT_LIMIT)]
            for row in self.spans:
                self.spans_by_trace[trace_key(row)].append(row)
            self.configs = {str(row.pk): row for row in CustomEvalConfig.objects.filter(project_id__in=self.projects, deleted=False)}
            if self.configs:
                self.evals = read_runtime_statement(self.session, builder.build_usage_evals(config_ids=self.configs, members=members), max_rows=FACT_LIMIT)
        return self

    def agent(self, key):
        return agent_identity(project_id=key[0], project_name=self.projects[key[0]], service_name=key[1], agent_name=key[2])

    def metrics(self, loads):
        if self.session.deadline.elapsed_ms() >= self.session.deadline.total_ms:
            raise SkillsHubError("runtime_read_budget_exceeded", "This took too long. Try a shorter window.", status=503)
        cache_key = frozenset((trace_key(row), row["span_id"]) for row in loads)
        if cache_key in self.metrics_cache:
            return self.metrics_cache[cache_key]
        members = {trace_key(row) for row in loads}
        traces = self.spans_by_trace
        versions = {str(row["skill_version_id"]) for row in loads}
        roots, costs, tokens, scope_costs, overhead = [], [], [], [], []
        any_errors = stamped = 0
        load_rows = [row for row in loads if row.get("is_load", True)]
        load_ids = {(trace_key(row), row["span_id"]) for row in load_rows}
        for member in members:
            spans = traces[member]
            root = sorted((row for row in spans if not row["parent_span_id"]), key=lambda row: (row["start_time"], row["id"]))
            if root:
                roots.append(root[0])
            any_errors += any(row["status"] == "ERROR" for row in spans)
            costs.append(sum(number(row["cost"]) for row in spans))
            tokens.append(sum(int(row["total_tokens"] or 0) for row in spans))
            scoped = [row for row in spans if (row["context_version_id"] in versions if self.query.get("_context_comparison") else versions.intersection(row["skill_version_ids"]))]
            stamped += bool(scoped)
            scope_costs.append(sum(number(row["cost"]) for row in scoped))
            overhead.extend(number(row["latency_ms"]) for row in spans if (member, row["id"]) in load_ids)
        failed = sum(row["status"] == "ERROR" for row in roots)
        coverage = stamped / len(members) if members else None
        # Multiple results/spans in a trace have equal trace weight: mean score,
        # and all results passing for the per-trace pass indicator. No averaging
        # per-agent rates or percentiles is used anywhere in these projections.
        evaluated = defaultdict(lambda: defaultdict(list))
        from tracer.services.clickhouse.eval_expressions import EVAL_TRUTHY_OUTPUTS
        for row in self.evals:
            config = self.configs[row["config_id"]]
            member = (str(config.project_id), row["trace_id"])
            if member not in members:
                continue
            if row["observation_span_id"] and not any(s["id"] == row["observation_span_id"] for s in traces[member]):
                continue
            raw = row["output_float"] if row["output_float"] is not None else row["output_bool"]
            text = (row["output_str"] or "").lower()
            if raw is None and not text:
                continue
            passed = (raw is not None and float(raw) >= 1) or text in EVAL_TRUTHY_OUTPUTS
            score = float(raw) if raw is not None else float(passed)
            if not isfinite(score):
                continue
            evaluated[row["config_id"]][member].append((score, passed))
        evals = []
        for config_id, results in sorted(evaluated.items()):
            evals.append({"config_id": config_id, "name": self.configs[config_id].name or "Evaluation",
                          "mean": mean([mean([v[0] for v in values]) for values in results.values()]),
                          "pass_rate": mean([float(all(v[1] for v in values)) for values in results.values()]),
                          "evaluated": len(results), "coverage": len(results) / len(members)})
        result = {
            "traces": len(members), "loads": len(load_rows), "incomplete": len(members) - len(roots),
            "failed_requests": {"count": failed, "rate": failed / len(roots) if roots else None, "denominator": len(roots)},
            "errors_any_span": any_errors,
            "latency_ms": {"p50": quantile([number(row["latency_ms"]) for row in roots], .5), "p95": quantile([number(row["latency_ms"]) for row in roots], .95)},
            "cost": {"total": sum(costs), "per_trace_mean": mean(costs), "per_trace_p50": quantile(costs, .5)},
            "tokens": {"total": sum(tokens), "per_trace_mean": mean(tokens), "per_trace_p50": quantile(tokens, .5)},
            "in_scope_cost": {"total": sum(scope_costs), "coverage": coverage} if coverage is not None and coverage >= .5 else None,
            "cached_share": mean([float(bool(row["cached"])) for row in load_rows if row["cached"] is not None]),
            "load_overhead_ms_p50": quantile(overhead, .5), "low_sample": len(members) < MIN_TRACES,
            "first_seen": min((row["start_time"] for row in loads), default=None),
            "last_seen": max((row["start_time"] for row in loads), default=None), "evals": evals,
        }
        self.metrics_cache[cache_key] = result
        return result

    def consumers(self):
        skills = [self.resource.pk] if self.resource else [s.pk for s in self.skills.values()]
        return [{"label": "", "channel": row.channel, "sdk_version": row.sdk_version,
                 "tracing_mode": row.tracing_mode, "can_record": can_record(row.sdk_version, row.tracing_mode, row.sdk_language)}
                for row in HubSkillUsageDaily.objects.filter(skill_id__in=skills).order_by("-last_at")[:100]]

    def links(self, loads, *, version_id=None, skill_id=None, agent=None, event_id=None, side=None):
        def item(column, value):
            return {"column_id": column, "filter_config": {"col_type": "SKILL", "filter_type": "text", "filter_op": "equals", "filter_value": value}}
        filters = []
        if version_id:
            filters.extend([item("skill.version_id", version_id), item("skill.digest", self.versions[version_id].digest)])
        elif skill_id:
            filters.append(item("skill.id", skill_id))
        if self.resource and self.resource.kind == "agent_context":
            filters.append(item("skill.context_id", str(self.resource.pk)))
        if self.query.get("context_version_id"):
            filters.append(item("skill.context_version_id", str(self.query["context_version_id"])))
        if self.query.get("label"):
            filters.append(item("skill.requested_label", self.query["label"]))
        if agent is None and ("service" in self.query or "agent" in self.query):
            agent = (str(self.query["project_id"]), self.query.get("service", ""), self.query.get("agent", ""))
        if agent:
            filters.extend([item("skill.service", agent[1]), item("skill.agent", agent[2])])
        if self.query.get("_context_comparison"):
            filters = [{"column_id": "fi.context.id", "filter_config": {"col_type": "SPAN_ATTRIBUTE", "filter_type": "text", "filter_op": "equals", "filter_value": str(self.resource.pk)}}]
        projects = {str(row["project_id"]) for row in loads}
        project = next(iter(projects)) if len(projects) == 1 else None
        base = {"project_id": project, "from": self.start, "to": self.end,
                "source": {"skill_id": skill_id, "version_id": version_id, "agent_key": agent, "event_id": event_id, "side": side, "metric": "all"}}
        all_link = {**base, "filters": filters}
        eval_links = {}
        from tracer.services.clickhouse.eval_expressions import EVAL_TRUTHY_OUTPUTS
        members = {trace_key(row) for row in loads}
        for config_id, config in self.configs.items():
            failed_ids = set()
            for row in self.evals:
                if row["config_id"] != config_id:
                    continue
                member = (str(config.project_id), row["trace_id"])
                if member not in members or (row["observation_span_id"] and not any(s["id"] == row["observation_span_id"] for s in self.spans_by_trace[member])):
                    continue
                score = row["output_float"] if row["output_float"] is not None else row["output_bool"]
                text = (row["output_str"] or "").lower()
                if (score is not None or text) and not ((score is not None and float(score) >= 1) or text in EVAL_TRUTHY_OUTPUTS):
                    failed_ids.add(row["trace_id"])
            if len(failed_ids) <= 200:
                eval_links[config_id] = {**base, "project_id": str(config.project_id), "filters": [*filters, {"column_id": "trace_id", "filter_config": {"col_type": "NORMAL", "filter_type": "text", "filter_op": "in", "filter_value": sorted(failed_ids)}}], "source": {**base["source"], "metric": f"eval:{config_id}"}}
        return {"project_id": project, "all": all_link,
                "failed": {**base, "filters": filters if self.query.get("_context_comparison") else [*filters, item("skill.root_status", "error")], "source": {**base["source"], "metric": "failed_requests"}},
                "slowest": {**base, "filters": filters, "sort": {"column_id": "latency", "direction": "desc"}, "source": {**base["source"], "metric": "latency_p95"}}, "eval_failed": eval_links}

    def metric_links(self, loads, *, event_id=None, side=None, version_id=None, agent=None, exact=False):
        skill_id = str(self.resource.pk) if self.resource and self.resource.kind == "skill" else None
        links = self.links(loads, version_id=version_id, skill_id=skill_id, agent=agent, event_id=event_id, side=side)
        projects = sorted({str(row["project_id"]) for row in loads})
        def project_link(link, project, kind):
            selected = [row for row in loads if str(row["project_id"]) == project]
            ids = sorted({row["trace_id"] for row in selected})
            if self.query.get("_context_comparison") and kind == "failed":
                failed = []
                for trace_id in ids:
                    roots = sorted((row for row in self.spans_by_trace[(project, trace_id)] if not row["parent_span_id"]), key=lambda row: (row["start_time"], row["id"]))
                    if roots and roots[0]["status"] == "ERROR":
                        failed.append(trace_id)
                ids = failed
            # Exact snapshot links are never silently shortened. Natural version
            # links remain available for larger pooled cohorts.
            if exact and len(ids) > 200:
                return None
            result = {**link, "project_id": project}
            if exact:
                result["filters"] = [*link["filters"], {"column_id": "trace_id", "filter_config": {"col_type": "NORMAL", "filter_type": "text", "filter_op": "in", "filter_value": ids}}]
            return result
        result = {}
        for metric, kind in (("traces", "all"), ("failed_requests", "failed"), ("latency_p95", "slowest"), ("cost_per_trace", "all")):
            options = []
            for project in projects:
                link = project_link(links[kind], project, kind)
                if link is not None:
                    metrics = self.metrics([row for row in loads if str(row["project_id"]) == project])
                    options.append({"projectId": project, "projectName": self.projects[project], "traces": metrics["traces"], "failedRequests": metrics["failed_requests"], "link": link})
            if len(options) == len(projects) and options:
                result[metric] = options[0]["link"] if len(options) == 1 else {**links[kind], "projects": options}
        result.update({f"eval:{config_id}": link for config_id, link in links["eval_failed"].items()})
        return result

    def row(self, loads, *, version_id=None, skill_id=None, agent=None, context_id=None):
        metrics = self.metrics(loads)
        version = self.versions.get(version_id)
        skill = version.skill if version else self.skills.get(skill_id)
        return {**metrics, "skill_id": str(skill.pk) if skill else None, "name": skill.name if skill else None,
                "version_id": version_id, "version": version.number if version else None,
                "labels_now": sorted(label.name for label in version.labels.all()) if version else [],
                "requested_labels": dict(Counter(row["requested_label"] for row in loads if row["requested_label"])),
                "requested_versions": dict(Counter(str(row["requested_version"]) for row in loads if row["requested_version"])),
                "digest_status": "verified", "agent": self.agent(agent) if agent else None,
                "context": {"id": context_id, "name": self.contexts[context_id].name} if context_id in self.contexts else None,
                "projects": [{"project_id": key, "project_name": self.projects[key], **self.metrics([row for row in loads if str(row["project_id"]) == key])} for key in sorted({str(row["project_id"]) for row in loads})],
                "links": self.links(loads, version_id=version_id, skill_id=str(skill.pk) if skill else None, agent=agent)}


def page_rows(rows, *, principal, query, projection):
    identity = query_identity(principal=principal, query=query, projection=projection)
    after = cursor_after(query=query, identity=identity)
    if after is not None:
        rows = [row for row in rows if row["page_key"] > after]
    page = rows[:query["limit"]]
    cursor = next_cursor(identity=identity, after=page[-1]["page_key"]) if len(rows) > len(page) else None
    return [{key: value for key, value in row.items() if key != "page_key"} for row in page], cursor


def usage(*, principal, workspace, query):
    # Validate the cursor before any CH work.
    cursor_after(query=query, identity=query_identity(principal=principal, query=query, projection="usage"))
    library = query["group_by"] == "skill" and query["to"] - query["from"] == timedelta(days=7)
    facts = RuntimeFacts(principal=principal, workspace=workspace, query=query).read(query["from"] - timedelta(days=2) if library else query["from"], query["to"])
    comparison_loads, comparison_start = facts.loads, facts.start
    if library:
        facts.start = query["from"]
        facts.loads = [row for row in facts.loads if row["start_time"] >= facts.start]
        facts.failed = [row for row in facts.failed if row["start_time"] >= facts.start]
        facts.mismatches = [row for row in facts.mismatches if row["start_time"] >= facts.start]
    groups = defaultdict(list)
    group_by = query["group_by"]
    for load in facts.loads:
        key = (str(load["skill_id"]),) if group_by == "skill" else (str(load["skill_version_id"]),)
        if group_by == "agent":
            key = (str(load["skill_version_id"]), *agent_key(load))
        elif group_by == "context":
            key = (str(load["context_id"]),)
            if key[0] not in facts.contexts:
                continue
        groups[key].append(load)
    fetches = defaultdict(list)
    if group_by == "skill":
        for key in facts.skills:
            groups.setdefault((key,), [])
        for bucket in HubSkillUsageDaily.objects.filter(skill_id__in=facts.skills, day__gte=(facts.end - timedelta(days=7)).date(), day__lte=facts.end.date()):
            fetches[str(bucket.skill_id)].append(can_record(bucket.sdk_version, bucket.tracing_mode, bucket.sdk_language))
    events, previous_events = {}, {}
    if group_by == "skill":
        from model_hub.models.skills_hub import HubSkillLabelEvent
        for event in HubSkillLabelEvent.objects.filter(skill_id__in=facts.skills, label="production", created_at__gte=comparison_start, created_at__lte=facts.end).order_by("-created_at", "-id"):
            key = str(event.skill_id)
            if key in events:
                previous_events.setdefault(key, event)
            else:
                events[key] = event
    rows = []
    for key, loads in groups.items():
        row = facts.row(loads, skill_id=key[0] if group_by == "skill" else None,
                        version_id=key[0] if group_by in {"version", "agent"} else None,
                        agent=key[1:] if group_by == "agent" else None, context_id=key[0] if group_by == "context" else None)
        row["page_key"] = f"{FACT_LIMIT - row['traces']:05d}:" + json.dumps(key, separators=(",", ":"))
        if group_by == "version":
            agents = defaultdict(list)
            for load in loads:
                agents[agent_key(load)].append(load)
            row["agent_rows"] = [facts.row(values, version_id=key[0], agent=agent) for agent, values in sorted(agents.items())]
        if group_by == "skill":
            capable = bool(loads) or any(fetches[key[0]])
            move = None
            event = events.get(key[0])
            if event:
                previous = previous_events.get(key[0])
                windows = move_windows(moved_at=event.created_at, now=facts.end, window=timedelta(days=1), previous_move_at=previous.created_at if previous and previous.created_at < event.created_at else None)
                old, new = defaultdict(list), defaultdict(list)
                version_ids = {v.number: str(v.pk) for v in facts.versions.values() if str(v.skill_id) == key[0]}
                for load in comparison_loads:
                    if str(load["skill_id"]) != key[0]:
                        continue
                    if windows["before"]["from"] <= load["start_time"] < event.created_at and str(load["skill_version_id"]) == version_ids.get(event.from_version):
                        old[agent_key(load)].append(load)
                    elif event.created_at <= load["start_time"] < windows["after"]["to"]:
                        new[agent_key(load)].append(load)
                verdict = regression_verdict({k: facts.metrics(v) for k, v in old.items()}, {k: facts.metrics(v) for k, v in new.items()}, complete=windows["complete"])
                # A narrower query cannot certify a completed before-window.
                if comparison_start > windows["before"]["from"] or facts.end < windows["after"]["to"]:
                    verdict = {"verdict": "not_enough_data" if windows["complete"] else "watching", "trigger": None}
                if facts.end <= windows["ends_at"] + timedelta(days=7):
                    move = {"event_id": str(event.pk), "to_version": event.to_version, "verdict": verdict["verdict"], "metric": (verdict["trigger"] or {}).get("metric"), "window_ends_at": windows["ends_at"]}
            row["runtime"] = {"loads": len(loads), "loaded_7d": row["traces"] if capable and facts.end - facts.start == timedelta(days=7) else None,
                              "agents": len({agent_key(load) for load in loads}), "can_record": capable, "move": move,
                              "regression": move["verdict"] in {"worse", "worse_so_far"} if move else False}
        rows.append(row)
    rows.sort(key=lambda row: row["page_key"])
    rows, cursor = page_rows(rows, principal=principal, query=query, projection="usage")
    summary = {**facts.metrics(facts.loads), "agents": len({agent_key(row) for row in facts.loads}), "projects": len({str(row["project_id"]) for row in facts.loads}), "same_agent_compare": None}
    consumers = facts.consumers()
    diagnosis = runtime_diagnosis(has_projects=bool(facts.projects), readable_projects=len(facts.projects), consumers=consumers, loads=len(facts.loads))
    result = {"rows": rows, "summary": summary,
              "coverage": {"projects_scanned": len(facts.projects), "projects_with_loads": summary["projects"], "unreadable_projects": 0, "instrumented": bool(facts.loads) or diagnosis["can_record"]},
              "diagnosis": diagnosis, "failed_loads": {"total": len(facts.failed), "by_error_type": dict(Counter(row["error_type"] for row in facts.failed)), "links": {}},
              "mismatch": {"loads": len(facts.mismatches), "traces": len({trace_key(row) for row in facts.mismatches}), "links": {}} if facts.mismatches else None, "move": None, "next_cursor": cursor, "totals_note": TOTALS_NOTE, "truncated": facts.truncated}
    for project_id in sorted({str(row["project_id"]) for row in facts.failed}):
        failed_rows = [row for row in facts.failed if str(row["project_id"]) == project_id]
        base = facts.links(failed_rows)["all"]
        names = sorted({row["skill_name"] for row in failed_rows})
        base["filters"] = [*base["filters"],
                           {"column_id": "skill.name", "filter_config": {"col_type": "SKILL", "filter_type": "text", "filter_op": "in", "filter_value": names}},
                           {"column_id": "skill.load_result", "filter_config": {"col_type": "SKILL", "filter_type": "text", "filter_op": "equals", "filter_value": "failed"}}]
        result["failed_loads"]["links"][project_id] = {"all": base}
    if facts.resource:
        event = facts.resource.label_events.filter(label="production").order_by("-created_at", "-id").first()
        if event:
            result["move"] = {"event_id": str(event.pk), "label": event.label, "from_version": event.from_version, "to_version": event.to_version}
    if "service" in query or "agent" in query:
        result["agent"] = facts.agent((str(query["project_id"]), query.get("service", ""), query.get("agent", "")))
        result["contexts"] = [{"id": key, "name": facts.contexts[key].name} for key in sorted({str(row["context_id"]) for row in facts.loads} & facts.contexts.keys())]
    if facts.resource and facts.resource.kind == "agent_context":
        dependencies = scoped_dependencies(principal, [facts.context_version] if facts.context_version else [])
        linked = {str(row.child_skill_id): row for row in dependencies}
        summary_loads = facts.loads
        if facts.projects and facts.end - facts.start != timedelta(days=7):
            builder = SkillRuntimeQueryBuilder(project_ids=facts.projects, start=facts.end - timedelta(days=7), end=facts.end)
            summary_loads = read_runtime_statement(facts.session, builder.build_usage_loads(
                identities=[(str(v.skill_id), str(v.pk), v.digest) for v in facts.versions.values()],
                skill_names=[s.name for s in facts.skills.values()], context_id=facts.resource.pk, label=query.get("label"),
                agent=(str(query["project_id"]), query.get("service", ""), query.get("agent", "")) if "service" in query or "agent" in query else None), max_rows=FACT_LIMIT)
            if query.get("context_version_id"):
                summary_loads = [row for row in summary_loads if str(row["context_version_id"]) == str(query["context_version_id"])]
        result["links_summary"] = [{"skill_id": key, "linked_version": dependency.child_version.number, "traces_7d": len({trace_key(row) for row in summary_loads if str(row["skill_id"]) == key}), "loaded_versions": sorted({facts.versions[str(row["skill_version_id"])].number for row in summary_loads if str(row["skill_id"]) == key}), "can_record": bool(summary_loads) or diagnosis["can_record"]} for key, dependency in linked.items()]
        loaded_versions = {str(row["skill_version_id"]) for row in facts.loads}
        linked_versions = {str(row.child_version_id) for row in linked.values()}
        result["linked_not_loaded"] = [{"skill_id": key, "name": row.child_skill.name, "version_id": str(row.child_version_id), "version": row.child_version.number} for key, row in linked.items() if str(row.child_version_id) not in loaded_versions]
        result["loaded_not_linked"] = [facts.row([row for row in facts.loads if str(row["skill_version_id"]) == version_id], version_id=version_id) for version_id in sorted(loaded_versions - linked_versions)]
        for row in result["rows"]:
            row["linked"] = row["version_id"] in linked_versions
    if query["projection"] == "series":
        result["series"] = []
        step = timedelta(hours=1) if query["bucket"] == "hour" else timedelta(days=1)
        for version_id in sorted({str(row["skill_version_id"]) for row in facts.loads}):
            points, start = [], facts.start
            while start < facts.end:
                metrics = facts.metrics([row for row in facts.loads if str(row["skill_version_id"]) == version_id and start <= row["start_time"] < start + step])
                value = metric_value(metrics, query["metric"])
                if value is not None:
                    points.append({"t": start, "value": value, "traces": metrics["traces"], "low_sample": metrics["low_sample"]})
                start += step
            result["series"].append({"version_id": version_id, "version": facts.versions[version_id].number, "points": points})
        result["markers"] = [{"event_id": str(event.pk), "label": event.label, "t": event.created_at, "from_version": event.from_version, "to_version": event.to_version}
                             for event in facts.resource.label_events.filter(created_at__gte=facts.start, created_at__lt=facts.end).order_by("created_at")[:200]] if facts.resource else []
    return result


def scoped_dependencies(principal, versions):
    return HubSkillContextDependency.objects.filter(
        organization_id=principal.scope.organization_id, workspace_id=principal.scope.workspace_id,
        parent_version__in=versions, child_skill__in=resources_for_scope(scope=principal.scope, kind="skill"),
    ).select_related("child_skill", "child_version")


def metric_value(metrics, name):
    if name.startswith("eval:"):
        return next((row["pass_rate"] for row in metrics["evals"] if row["config_id"] == name[5:]), None)
    return {"traces": metrics["traces"], "failed_requests": metrics["failed_requests"]["rate"],
            "latency_p95": metrics["latency_ms"]["p95"], "cost_per_trace": metrics["cost"]["per_trace_mean"]}[name]


def compare_metrics(before, after):
    low = min(before["traces"], after["traces"]) < MIN_TRACES
    result = []
    for name in ("failed_requests", "latency_p95", "cost_per_trace", "traces"):
        a, b = metric_value(before, name), metric_value(after, name)
        delta = None
        if not low and a is not None and b is not None:
            # Rates render as percentage points; latency/cost/count render as
            # relative percentages. A zero baseline has no finite relative delta.
            delta = b - a if name == "failed_requests" else (b - a) / a if a else None
        result.append({"metric": name, "before": a, "after": b, "delta": delta,
                       "low_sample": low, "before_traces": before["traces"], "after_traces": after["traces"],
                       "before_count": before["failed_requests"]["count"] if name == "failed_requests" else None,
                       "after_count": after["failed_requests"]["count"] if name == "failed_requests" else None,
                       "before_denominator": before["failed_requests"]["denominator"] if name == "failed_requests" else before["traces"],
                       "after_denominator": after["failed_requests"]["denominator"] if name == "failed_requests" else after["traces"]})
    old, new = ({row["config_id"]: row for row in metrics["evals"]} for metrics in (before, after))
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        eligible = not low and a and b and min(a["evaluated"], b["evaluated"]) >= MIN_EVALUATED
        result.append({"metric": f"eval:{key}", "name": (b or a)["name"], "before": a["pass_rate"] if a else None, "after": b["pass_rate"] if b else None,
                       "delta": b["pass_rate"] - a["pass_rate"] if eligible else None, "low_sample": not bool(eligible),
                       "before_traces": before["traces"], "after_traces": after["traces"], "before_coverage": a["coverage"] if a else 0, "after_coverage": b["coverage"] if b else 0})
    return result


def comparison(*, principal, workspace, query):
    cursor_after(query=query, identity=query_identity(principal=principal, query=query, projection="comparison"))
    resource = get_resource(principal=principal, resource_id=query["resource_id"], kind=query["kind"])
    event = None
    if "before_from" in query:
        windows = {"before": {"from": query["before_from"], "to": query["before_to"]}, "after": {"from": query["after_from"], "to": query["after_to"]},
                   "complete": True, "ends_at": query["after_to"], "before_cut_at": None, "hours_elapsed": 0, "hours_total": 0}
    else:
        events = resource.label_events.filter(label=query["label"])
        event = events.filter(pk=query["event_id"]).first() if query.get("event_id") else events.order_by("-created_at", "-id").first()
        if event is None and query.get("event_id"):
            raise SkillsHubError("runtime_event_not_found", "This label move cannot be opened.", status=404)
        now = timezone.now()
        hours = int(query["window"][:-1]) * (24 if query["window"][-1] == "d" else 1)
        previous = events.filter(created_at__lt=event.created_at).order_by("-created_at").first() if event else None
        windows = move_windows(moved_at=event.created_at if event else now, now=now, window=timedelta(hours=hours), previous_move_at=previous.created_at if previous else None)
    base = {key: value for key, value in query.items() if key in {"resource_id", "kind", "project_ids"}}
    if resource.kind == "agent_context":
        base["_context_comparison"] = True
    session = RuntimeReadSession(V2AnalyticsQueryService(), deadline=ReadDeadline.start(3000))
    before = RuntimeFacts(principal=principal, workspace=workspace, query=base, session=session).read(**{"start": windows["before"]["from"], "end": windows["before"]["to"]})
    after = RuntimeFacts(principal=principal, workspace=workspace, query=base, session=session).read(**{"start": windows["after"]["from"], "end": windows["after"]["to"]})
    if event is None and "before_from" not in query:
        before.loads, after.loads = [], []
        windows.update(complete=True, ends_at=None)
    versions = {row.number: str(row.pk) for row in resource.versions.all()}
    old_id = versions.get(event.from_version) if event else None
    new_id = versions.get(event.to_version) if event else None
    version_field = "context_version_id" if resource.kind == "agent_context" else "skill_version_id"
    old_loads = [row for row in before.loads if str(row[version_field]) == old_id] if event else before.loads
    new_loads = [row for row in after.loads if str(row[version_field]) == new_id] if event else after.loads
    before_agents, after_agents = defaultdict(list), defaultdict(list)
    for row in old_loads:
        before_agents[agent_key(row)].append(row)
    for row in after.loads:
        after_agents[agent_key(row)].append(row)
    common = before_agents.keys() & after_agents.keys()
    old_metrics = {key: before.metrics(rows) for key, rows in before_agents.items()}
    new_metrics = {key: after.metrics(rows) for key, rows in after_agents.items()}
    verdict = regression_verdict(old_metrics, new_metrics, complete=windows["complete"])
    pooled_before = before.metrics([row for row in old_loads if agent_key(row) in common])
    pooled_after = after.metrics([row for row in after.loads if agent_key(row) in common])
    rows = [{"agent": before.agent(key), "metrics": compare_metrics(old_metrics.get(key, before.metrics([])), new_metrics.get(key, after.metrics([]))), "page_key": json.dumps(key, separators=(",", ":"))} for key in sorted(before_agents.keys() | after_agents.keys())]
    rows, cursor = page_rows(rows, principal=principal, query=query, projection="comparison")
    visible = {tuple(row["agent"]["key"]) for row in rows}
    dependencies = list(scoped_dependencies(principal, HubSkillVersion.objects.filter(skill_id__in=before.contexts)))
    rules = {str(row.parent_version_id): "pinned" if "version" in row.requested_selector else "following" for row in dependencies if row.child_skill_id == resource.pk}
    adoption = [{"agent": after.agent(key), **adoption_state([{**row, "skill_version_id": row[version_field]} for row in after_agents.get(key, [])], to_version_id=new_id, context_rules=rules)} for key in sorted(visible)] if event else []
    changed = []
    if event and resource.kind == "agent_context":
        def closure(identity):
            return {str(row.child_skill_id): {"name": row.child_skill.name, "version_id": str(row.child_version_id), "version": row.child_version.number} for row in dependencies if str(row.parent_version_id) == identity}
        changed = closure_diff(closure(old_id), closure(new_id))
    control_loads = [row for row in after.loads if str(row[version_field]) == old_id] if event else []
    control = {"version": event.from_version, "agents": len({agent_key(row) for row in control_loads}), "metrics": compare_metrics(before.metrics([]), after.metrics(control_loads))} if control_loads else None
    all_before, all_after = before.metrics(old_loads), after.metrics(new_loads)
    event_id = str(event.pk) if event else None
    old_version = old_id if resource.kind == "skill" else None
    new_version = new_id if resource.kind == "skill" else None
    all_links = {"before": before.metric_links(old_loads, event_id=event_id, side="before", version_id=old_version, exact=old_version is None),
                 "after": after.metric_links(new_loads, event_id=event_id, side="after", version_id=new_version, exact=new_version is None)}
    same_links = {"before": before.metric_links([row for row in old_loads if agent_key(row) in common], event_id=event_id, side="before", version_id=old_version, exact=True),
                  "after": after.metric_links([row for row in after.loads if agent_key(row) in common], event_id=event_id, side="after", exact=True)}
    for row in rows:
        key = tuple(row["agent"]["key"])
        row["links"] = {"before": before.metric_links(before_agents.get(key, []), event_id=event_id, side="before", version_id=old_version, agent=key, exact=True),
                        "after": after.metric_links(after_agents.get(key, []), event_id=event_id, side="after", agent=key, exact=True)}
    if control:
        control["links"] = {"after": after.metric_links(control_loads, event_id=event_id, side="after", version_id=old_version, exact=old_version is None)}
    return {"event_id": str(event.pk) if event else None, "label": query["label"], "from_version": event.from_version if event else None, "to_version": event.to_version if event else None,
            "from_version_id": old_id, "to_version_id": new_id, "moved_at": event.created_at if event else None, "window": query["window"],
            "before_cut_at": windows["before_cut_at"], "ends_at": windows["ends_at"], "complete": windows["complete"],
            **{key: value for key, value in verdict.items() if key != "by_agent"},
            "collecting": {"hours_elapsed": windows["hours_elapsed"], "hours_total": windows["hours_total"], "traces_so_far": all_after["traces"]} if not windows["complete"] and not verdict["so_far"] else None,
            "same_agents": {"agents": len(common), "metrics": compare_metrics(pooled_before, pooled_after), "links": same_links},
            "all_agents": {"metrics": compare_metrics(all_before, all_after), "links": all_links}, "control": control,
            "by_agent": [row for row in rows if tuple(row["agent"]["key"]) in common], "adoption": adoption, "changed_skills": changed, "links": all_links,
            "next_cursor": cursor, "truncated": before.truncated or after.truncated, "totals_note": TOTALS_NOTE}
