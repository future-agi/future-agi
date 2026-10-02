"""Scoped, captured PostgreSQL inputs for local query replay, not mocked results.

Candidate SQL and ClickHouse result execution remain real. The snapshot replaces
only relational metadata I/O; its cost is reported separately from query timing.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import replay_observe_filters as replay


def metadata_scope(scope):
    """Compare ownership, not display labels or the requested end-user filter.

    ``user_id`` in the replay plan selects observed traces; it is not the
    authenticated principal. Captured metadata is owned by the organization,
    workspace and project. Unknown scope fields still participate in equality.
    """
    if not isinstance(scope, dict) or any(
        not isinstance(scope.get(key), str) or not scope[key].strip()
        for key in ("organization_id", "workspace_id", "project_id")
    ):
        raise replay.ReplayError("RELATIONAL_METADATA_SCOPE_MISMATCH")
    return {
        key: value
        for key, value in scope.items()
        if key not in ("project_label", "customer_label", "user_id")
    }


class SnapshotMetadata:
    def __init__(self, document, scope, projects):
        if (
            document.get("transaction_read_only") is not True
            or metadata_scope(document.get("scope")) != metadata_scope(scope)
            or set(document.get("authorized_projects", [])) != set(projects)
            or not document.get("captured_at")
        ):
            raise replay.ReplayError("RELATIONAL_METADATA_SCOPE_MISMATCH")
        self.document, self.scope = document, scope
        self.projects = set(projects)
        self.fingerprint = replay.digest(document)
        self.evals = document["eval_config_inventory"]
        self.labels = document["annotation_inventory"]
        if any(str(row["project_id"]) not in self.projects for row in self.evals):
            raise replay.ReplayError("EVAL_METADATA_PROJECT_MISMATCH")
        if any(
            not set(row.get("project_ids", [])) <= self.projects for row in self.labels
        ):
            raise replay.ReplayError("ANNOTATION_METADATA_PROJECT_MISMATCH")

    def _projects(self, projects):
        if not projects or not set(map(str, projects)) <= self.projects:
            raise replay.ReplayError("METADATA_PROJECTS_REQUIRED_AND_AUTHORIZED")
        return set(map(str, projects))

    def eval_metadata(self, eval_id, projects):
        from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata

        selected = self._projects(projects)
        matching = [row for row in self.evals if row["config_id"] == str(eval_id)]
        if not matching:
            matching = [row for row in self.evals if row["template_id"] == str(eval_id)]
        matching = [row for row in matching if row["project_id"] in selected]
        output = "SCORE"
        if matching and not matching[0].get("template_deleted"):
            normalized = (
                (matching[0].get("config_output") or "")
                .upper()
                .replace("/", "_")
                .replace(" ", "_")
            )
            if normalized in ("SCORE", "PASS_FAIL", "CHOICE", "CHOICES"):
                output = normalized
        return EvalFilterMetadata(tuple(row["config_id"] for row in matching), output)

    def labels_for_project(self, project_id, **kwargs):
        selected = self._projects(kwargs.get("project_ids") or [project_id])
        return [
            SimpleNamespace(id=row["label_id"])
            for row in self.labels
            if selected.intersection(row.get("project_ids", []))
        ]

    def label_map(self, projects):
        return {
            project: [str(row.id) for row in self.labels_for_project(project)]
            for project in sorted(self._projects(projects))
        }

    def builder_kwargs(self, project_id):
        selected = self._projects([project_id])
        ids = {row["config_id"] for row in self.evals if row["project_id"] in selected}
        ids.update(
            row["template_id"] for row in self.evals if row["project_id"] in selected
        )
        return {
            "annotation_label_ids": self.label_map([project_id])[project_id],
            "eval_filter_metadata": {
                key: self.eval_metadata(key, [project_id]) for key in ids
            },
        }

    @contextmanager
    def metadata_io(self):
        from tracer.services.clickhouse.query_builders import filters
        from tracer.services.clickhouse import exact_graph_reads
        from tracer.services import users_list_manager

        # Frozen real PG records, not artificial QuerySets or result rows. Any
        # other ORM dependency still fails on the dummy DB and stays untested.
        with (
            patch.object(filters, "resolve_eval_filter_metadata", self.eval_metadata),
            patch.object(
                users_list_manager, "resolve_eval_filter_metadata", self.eval_metadata
            ),
            patch.object(
                exact_graph_reads,
                "get_annotation_labels_for_project",
                self.labels_for_project,
            ),
        ):
            yield


def annotation_properties(document, project_id):
    """Inventory every visible label; positive inputs come only from real Scores."""
    properties = []
    kinds = {
        "text": "text",
        "numeric": "number",
        "star": "number",
        "categorical": "categorical",
        "thumbs_up_down": "thumbs",
    }
    for label in document["annotation_inventory"]:
        if project_id not in label.get("project_ids", []):
            continue
        kind = kinds.get(label["type"], label["type"])
        rows = [
            row
            for row in document.get("annotation_scores_snapshot", [])
            if row["project_id"] == project_id
            and row["label_id"] == label["label_id"]
            and not row["deleted"]
        ]
        seeds, annotators = {}, set()
        for row in rows:
            value = row["value"]
            if not isinstance(value, dict):
                continue
            key = {"text": "text", "categorical": "selected"}.get(kind, "value")
            actual = (
                value.get("rating", value.get("value"))
                if kind == "number"
                else value.get(key)
            )
            if replay.seed_valid(kind, actual):
                seeds[replay.canonical(actual)] = {"type": kind, "value": actual}
            if row.get("annotator_id"):
                annotators.add(str(row["annotator_id"]))
        base = {
            "name": label["name"],
            "column_id": label["label_id"],
            "col_type": "ANNOTATION",
            "source": "traces",
            "observed_types": [kind],
            "resolved_type": kind,
            "seeds": list(seeds.values()),
        }
        properties.append(base)
        properties.append(
            {
                **base,
                "name": label["name"] + " annotator",
                "column_id": label["label_id"] + "**annotator",
                "observed_types": ["annotator"],
                "resolved_type": "annotator",
                "seeds": [
                    {"type": "annotator", "value": value}
                    for value in sorted(annotators)
                ],
            }
        )
    return properties


def main():
    import argparse
    from collections import Counter

    parser = argparse.ArgumentParser(
        description="Build a scoped annotation matrix from real PG inputs"
    )
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--inventory-output", required=True)
    parser.add_argument("--plan-output", required=True)
    parser.add_argument("--canary-output", required=True)
    args = parser.parse_args()
    document = replay.read_json(args.metadata)
    SnapshotMetadata(document, document["scope"], document["authorized_projects"])
    properties = annotation_properties(document, document["scope"]["project_id"])
    inventory = {
        "scope": document["scope"],
        "attributes": [],
        "properties": properties,
        "source_metadata_sha256": replay.digest(document),
        "annotation_inventory_verified_complete": True,
    }
    plan = replay.make_plan(inventory, replay.utc(args.end))
    chosen = []
    # Declared canaries do not stand in for full per-property/operator coverage.
    for kind in ("text", "categorical", "thumbs", "annotator"):
        for surface in ("traces", "spans", "sessions", "trace_graph"):
            candidate = next(
                (
                    case
                    for case in plan["cases"]
                    if not case["blocked"]
                    and case["period"] == "12M"
                    and case["surface"] == surface
                    and case["variant"].startswith(kind + ":equals:")
                ),
                None,
            )
            if candidate:
                chosen.append(candidate["id"])
    replay.private_write(args.inventory_output, inventory)
    replay.private_write(args.plan_output, plan)
    replay.private_write(args.canary_output, chosen)
    print(
        replay.canonical(
            {
                "properties": len(properties),
                "planned": len(plan["cases"]),
                "kinds": dict(Counter(prop["resolved_type"] for prop in properties)),
                "canaries": len(chosen),
                "qualification": "NOT_TESTED",
            }
        )
    )


if __name__ == "__main__":
    main()
