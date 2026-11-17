"""Persist a dataset execution identity and aggregate its stored jury verdicts."""

import json
from itertools import combinations
from math import sqrt

from model_hub.models.evaluation import Evaluation, StatusChoices


def begin_run(runner, rows):
    if not (runner.template.config or {}).get("jury"):
        return None
    return Evaluation.objects.create(
        user=runner.user_eval_metric.user,
        organization=runner.organization,
        workspace=runner.workspace,
        eval_template=runner.template,
        status=StatusChoices.PROCESSING,
        input_data={
            "dataset_id": str(runner.dataset.id),
            "row_ids": [str(row.id) for row in rows],
        },
        eval_config={
            "metric_id": str(runner.user_eval_metric.id),
            "version_id": str(runner.jury_version.id) if runner.jury_version else None,
            "experiment_id": str(runner.experiment_dataset.experiment_id)
            if getattr(runner.experiment_dataset, "experiment_id", None)
            else None,
        },
        data={"jury_dataset_run": True, "definition": runner.template.config["jury"]},
    )


def decode_jury_mapping(mapping):
    result = dict(mapping)
    for key in ("steps", "checks"):
        value = result.get(key)
        if isinstance(value, str):
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError:
                # The executor records missing checks as unscored and rejects
                # malformed steps; neither can become an accidental pass.
                result[key] = None
    return result


def pair_stats(a, b, rows):
    values = []
    for row in rows:
        seats = {seat["id"]: seat.get("passed") for seat in row["seats"]}
        x, y = seats.get(a), seats.get(b)
        if isinstance(x, bool) and isinstance(y, bool):
            values.append((x, y))
    n = len(values)
    if not n:
        return {
            "a": a,
            "b": b,
            "n": 0,
            "agreement": None,
            "ci": None,
            "kappa": None,
            "low_sample": True,
        }
    agreement = sum(x == y for x, y in values) / n
    pa, pb = sum(x for x, _ in values) / n, sum(y for _, y in values) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    z = 1.959963984540054
    denom = 1 + z * z / n
    center = (agreement + z * z / (2 * n)) / denom
    radius = z * sqrt(agreement * (1 - agreement) / n + z * z / (4 * n * n)) / denom
    return {
        "a": a,
        "b": b,
        "n": n,
        "agreement": agreement,
        "ci": [max(0, center - radius), min(1, center + radius)],
        "kappa": (agreement - chance) / (1 - chance) if chance < 1 else None,
        "low_sample": n < 30,
    }


def aggregate_run(header):
    records = Evaluation.objects.filter(
        organization_id=header.organization_id,
        workspace_id=header.workspace_id,
        eval_template_id=header.eval_template_id,
        deleted=False,
        eval_config__jury_run_id=str(header.id),
    )
    by_row = {
        record.input_data["row_id"]: record
        for record in records.order_by("created_at", "id")
    }
    rows = []
    for index, row_id in enumerate(header.input_data["row_ids"]):
        record = by_row.get(row_id)
        snapshot = (record.data or {}).get("jury", {}) if record else {}
        rows.append(
            {
                "row_id": row_id,
                "row_number": index + 1,
                "evaluation_id": str(record.id) if record else None,
                "seats": snapshot.get("seats", []),
                "complete": snapshot.get("complete", False),
                "disagreement": snapshot.get("disagreement", False),
                "aggregate_score": snapshot.get("aggregate_score"),
            }
        )
    seats = header.data["definition"]["seats"]
    children = []
    for seat in seats:
        verdicts = [
            s.get("passed")
            for row in rows
            for s in row["seats"]
            if s["id"] == seat["id"] and isinstance(s.get("passed"), bool)
        ]
        children.append(
            {
                "id": seat["id"],
                "name": seat["id"],
                "judge_model": seat.get("model"),
                "n": len(verdicts),
                "pass_rate": sum(verdicts) / len(verdicts) if verdicts else None,
            }
        )
    return {
        "run_id": str(header.id),
        "kind": "dataset",
        "dataset_id": header.input_data["dataset_id"],
        "experiment_id": header.eval_config.get("experiment_id"),
        "version_id": header.eval_config.get("version_id"),
        "n": len(rows),
        "complete": all(row["complete"] for row in rows),
        "rows": rows,
        "children": children,
        "pairs": [
            pair_stats(a["id"], b["id"], rows) for a, b in combinations(seats, 2)
        ],
        "split_rows": [row["row_id"] for row in rows if row["disagreement"]],
    }
