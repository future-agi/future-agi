"""Deterministic CI comments contain identities and scores, never row contents."""


def render_ci_comment(result, regressions, query):
    totals = {
        state: sum(evaluator[state] for evaluator in result["evaluators"].values())
        for state in ("improved", "regressed", "unchanged", "not_comparable")
    }
    ranked = sorted(
        regressions,
        key=lambda row: (
            -abs(row["score"] - row["baseline_score"]),
            row["example_id"],
            row["metric_id"],
        ),
    )[: query["top_n"]]
    results_path = f"/dashboard/develop/experiment/{result['experiment_id']}/data"
    verdict = "PASS" if result["passed"] else "FAIL"
    lines = [
        f"## Evaluation CI: {verdict}",
        f"Regressed comparisons: **{result['regressed_total']}** "
        f"(allowed: **{query['max_regressed']}**).",
        f"Run: `{result['run_id']}`",
        f"Baseline: `{result['baseline_run_id']}`",
        "",
        f"Improved: **{totals['improved']}** | "
        f"Regressed: **{totals['regressed']}** | "
        f"Unchanged: **{totals['unchanged']}** | "
        f"Not comparable: **{totals['not_comparable']}**",
        "Counts are row/evaluator comparisons, not unique rows.",
        "",
    ]
    if ranked:
        lines.extend(
            [
                f"### Regressed rows (showing {len(ranked)} "
                f"of {result['regressed_total']})",
                "| Row ID | Evaluator ID | Baseline | Run |",
                "| --- | --- | ---: | ---: |",
            ]
        )
        for row in ranked:
            baseline = str(row["baseline_score"] or 0).removesuffix(".0")
            candidate = str(row["score"] or 0).removesuffix(".0")
            lines.append(
                f"| `{row['example_id']}` | `{row['metric_id']}` | "
                f"{baseline} | {candidate} |"
            )
        lines.extend(
            [
                "",
                "Sorted by absolute score change, then row ID and evaluator ID.",
                "Scores retain comparison precision; "
                "categorical scores are pass rates.",
            ]
        )
    else:
        lines.append("No regressed row/evaluator comparisons.")
    lines.extend(["", f"[View experiment Results]({results_path})", ""])
    return {
        "body": "\n".join(lines),
        "passed": result["passed"],
        "results_path": results_path,
    }
