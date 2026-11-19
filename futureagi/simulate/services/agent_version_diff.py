"""
Agent Version Diff & Changelog Service.

Provides pure-function and model-level utilities to compare two AgentVersion instances:
- System prompt and instruction textual diffs (unified diff and structured line chunks)
- Configuration parameters diff (model, temperature, provider, credentials, languages, etc.)
- Performance delta calculation (overall score, pass rate, test count, and per-template metrics)
- Chronological changelog summary generation across the version history
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simulate.models import AgentDefinition, AgentVersion


def compute_prompt_diff(
    text_a: str | None,
    text_b: str | None,
    label_a: str = "Base",
    label_b: str = "Target",
) -> dict[str, Any]:
    """Compute line-level unified diff and structured line chunks between two prompt texts."""
    str_a = (text_a or "").strip()
    str_b = (text_b or "").strip()

    lines_a = str_a.splitlines(keepends=True) if str_a else []
    lines_b = str_b.splitlines(keepends=True) if str_b else []

    unified = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=label_a,
            tofile=label_b,
            lineterm="",
        )
    )

    # Build structured line-by-line chunks for easy UI rendering
    chunks = []
    added_count = 0
    removed_count = 0

    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in lines_a[i1:i2]:
                chunks.append({"type": "unchanged", "line": line.rstrip("\r\n")})
        elif tag == "delete":
            for line in lines_a[i1:i2]:
                chunks.append({"type": "removed", "line": line.rstrip("\r\n")})
                removed_count += 1
        elif tag == "insert":
            for line in lines_b[j1:j2]:
                chunks.append({"type": "added", "line": line.rstrip("\r\n")})
                added_count += 1
        elif tag == "replace":
            for line in lines_a[i1:i2]:
                chunks.append({"type": "removed", "line": line.rstrip("\r\n")})
                removed_count += 1
            for line in lines_b[j1:j2]:
                chunks.append({"type": "added", "line": line.rstrip("\r\n")})
                added_count += 1

    has_changes = str_a != str_b

    return {
        "has_changes": has_changes,
        "added_lines": added_count,
        "removed_lines": removed_count,
        "unified_diff": "\n".join(unified),
        "chunks": chunks,
    }


def compute_parameter_diff(
    snapshot_a: dict[str, Any] | None,
    snapshot_b: dict[str, Any] | None,
    ignored_keys: tuple[str, ...] = (
        "id",
        "description",
        "commit_message",
        "created_at",
        "updated_at",
        "deleted",
        "deleted_at",
    ),
) -> list[dict[str, Any]]:
    """Compare two configuration snapshot dictionaries and identify parameter differences."""
    snap_a = snapshot_a or {}
    snap_b = snapshot_b or {}

    all_keys = sorted(
        (set(snap_a.keys()) | set(snap_b.keys())) - set(ignored_keys)
    )

    changes = []
    for key in all_keys:
        val_a = snap_a.get(key)
        val_b = snap_b.get(key)

        # Skip keys where both values are equivalent or empty
        if val_a == val_b:
            continue

        if key not in snap_a:
            change_type = "added"
        elif key not in snap_b:
            change_type = "removed"
        else:
            change_type = "modified"

        changes.append(
            {
                "key": key,
                "change_type": change_type,
                "base_value": val_a,
                "target_value": val_b,
            }
        )

    return changes


def compute_performance_delta(
    version_a: AgentVersion,
    version_b: AgentVersion,
    include_template_evals: bool = True,
) -> dict[str, Any]:
    """Calculate the difference in performance metrics between base and target versions."""
    score_a = float(version_a.score) if version_a.score is not None else None
    score_b = float(version_b.score) if version_b.score is not None else None
    score_delta = (
        round(score_b - score_a, 2)
        if score_a is not None and score_b is not None
        else None
    )

    pass_rate_a = (
        float(version_a.pass_rate) if version_a.pass_rate is not None else None
    )
    pass_rate_b = (
        float(version_b.pass_rate) if version_b.pass_rate is not None else None
    )
    pass_rate_delta = (
        round(pass_rate_b - pass_rate_a, 2)
        if pass_rate_a is not None and pass_rate_b is not None
        else None
    )

    tests_a = version_a.test_count or 0
    tests_b = version_b.test_count or 0
    test_count_delta = tests_b - tests_a

    template_deltas: list[dict[str, Any]] = []

    if include_template_evals:
        try:
            summary_a = _get_version_eval_summary(version_a)
            summary_b = _get_version_eval_summary(version_b)

            map_a = {item.get("template_id"): item for item in summary_a if item.get("template_id")}
            map_b = {item.get("template_id"): item for item in summary_b if item.get("template_id")}

            all_template_ids = set(map_a.keys()) | set(map_b.keys())
            for t_id in all_template_ids:
                item_a = map_a.get(t_id, {})
                item_b = map_b.get(t_id, {})
                name = item_b.get("template_name") or item_a.get("template_name") or str(t_id)

                val_a = item_a.get("average_score")
                val_b = item_b.get("average_score")

                val_a_float = float(val_a) if val_a is not None else None
                val_b_float = float(val_b) if val_b is not None else None
                delta_val = (
                    round(val_b_float - val_a_float, 2)
                    if val_a_float is not None and val_b_float is not None
                    else None
                )

                template_deltas.append(
                    {
                        "template_id": t_id,
                        "template_name": name,
                        "base_average_score": val_a_float,
                        "target_average_score": val_b_float,
                        "score_delta": delta_val,
                    }
                )
        except Exception:
            # Metric comparisons are non-blocking / best-effort
            pass

    return {
        "score": {
            "base": score_a,
            "target": score_b,
            "delta": score_delta,
        },
        "pass_rate": {
            "base": pass_rate_a,
            "target": pass_rate_b,
            "delta": pass_rate_delta,
        },
        "test_count": {
            "base": tests_a,
            "target": tests_b,
            "delta": test_count_delta,
        },
        "template_deltas": template_deltas,
    }


def _get_version_eval_summary(version: AgentVersion) -> list[dict[str, Any]]:
    """Helper to retrieve template eval summaries for a version."""
    try:
        from simulate.utils.eval_summary import (
            _build_template_statistics,
            _calculate_final_template_summaries,
            _get_completed_call_executions_for_agent_version,
            _get_eval_config_for_agent_version,
        )

        eval_configs = _get_eval_config_for_agent_version(version)
        if not eval_configs:
            return []
        call_executions = _get_completed_call_executions_for_agent_version(version)
        template_stats = _build_template_statistics(eval_configs, call_executions)
        return _calculate_final_template_summaries(template_stats)
    except Exception:
        return []


def generate_change_summary(
    prompt_diff: dict[str, Any],
    parameter_diffs: list[dict[str, Any]],
    perf_delta: dict[str, Any],
) -> list[str]:
    """Generate concise human-readable bullet points summarizing differences between two versions."""
    bullets: list[str] = []

    if prompt_diff.get("has_changes"):
        added = prompt_diff.get("added_lines", 0)
        removed = prompt_diff.get("removed_lines", 0)
        bullets.append(f"Prompt updated (+{added}, -{removed} lines)")

    for change in parameter_diffs:
        key = change.get("key")
        old_val = change.get("base_value")
        new_val = change.get("target_value")
        c_type = change.get("change_type")

        if key == "description":
            # Description is handled by prompt_diff if used as instructions
            continue

        if c_type == "modified":
            bullets.append(f"{key} changed from '{old_val}' to '{new_val}'")
        elif c_type == "added":
            bullets.append(f"Added {key}: '{new_val}'")
        elif c_type == "removed":
            bullets.append(f"Removed {key} (was '{old_val}')")

    score_delta = perf_delta.get("score", {}).get("delta")
    if score_delta is not None and score_delta != 0:
        sign = "+" if score_delta > 0 else ""
        bullets.append(f"Overall score changed by {sign}{score_delta}")

    pass_delta = perf_delta.get("pass_rate", {}).get("delta")
    if pass_delta is not None and pass_delta != 0:
        sign = "+" if pass_delta > 0 else ""
        bullets.append(f"Pass rate changed by {sign}{pass_delta}%")

    if not bullets:
        bullets.append("No material configuration changes detected.")

    return bullets


def compare_agent_versions(
    version_a: AgentVersion,
    version_b: AgentVersion,
    include_template_evals: bool = True,
) -> dict[str, Any]:
    """Perform a full comparison between base (version_a) and target (version_b)."""
    snap_a = version_a.configuration_snapshot or {}
    snap_b = version_b.configuration_snapshot or {}

    prompt_a = snap_a.get("description") or version_a.description or ""
    prompt_b = snap_b.get("description") or version_b.description or ""

    label_a = f"v{version_a.version_number}"
    label_b = f"v{version_b.version_number}"

    prompt_diff = compute_prompt_diff(prompt_a, prompt_b, label_a=label_a, label_b=label_b)
    parameter_diffs = compute_parameter_diff(snap_a, snap_b)
    perf_delta = compute_performance_delta(
        version_a, version_b, include_template_evals=include_template_evals
    )
    summary = generate_change_summary(prompt_diff, parameter_diffs, perf_delta)

    return {
        "base_version": {
            "id": str(version_a.id),
            "version_number": version_a.version_number,
            "version_name": version_a.version_name or f"v{version_a.version_number}",
            "status": version_a.status,
            "score": float(version_a.score) if version_a.score is not None else None,
            "pass_rate": float(version_a.pass_rate) if version_a.pass_rate is not None else None,
            "test_count": version_a.test_count or 0,
            "commit_message": version_a.commit_message,
            "created_at": version_a.created_at.isoformat() if version_a.created_at else None,
        },
        "target_version": {
            "id": str(version_b.id),
            "version_number": version_b.version_number,
            "version_name": version_b.version_name or f"v{version_b.version_number}",
            "status": version_b.status,
            "score": float(version_b.score) if version_b.score is not None else None,
            "pass_rate": float(version_b.pass_rate) if version_b.pass_rate is not None else None,
            "test_count": version_b.test_count or 0,
            "commit_message": version_b.commit_message,
            "created_at": version_b.created_at.isoformat() if version_b.created_at else None,
        },
        "prompt_diff": prompt_diff,
        "parameter_diffs": parameter_diffs,
        "performance_delta": perf_delta,
        "summary": summary,
    }


def get_agent_changelog(agent_definition: AgentDefinition) -> list[dict[str, Any]]:
    """Retrieve full chronological changelog across all versions of an AgentDefinition."""
    from simulate.models import AgentVersion

    versions = list(
        AgentVersion.objects.filter(
            agent_definition=agent_definition,
            deleted=False,
        ).order_by("-version_number")
    )

    if not versions:
        return []

    # Map version_number -> version object for quick lookup of prior version
    version_map = {v.version_number: v for v in versions}
    changelog_entries = []

    for v in versions:
        prior_version = version_map.get(v.version_number - 1)

        changes_summary: list[str] = []
        if prior_version:
            snap_curr = v.configuration_snapshot or {}
            snap_prior = prior_version.configuration_snapshot or {}

            prompt_curr = snap_curr.get("description") or v.description or ""
            prompt_prior = snap_prior.get("description") or prior_version.description or ""

            p_diff = compute_prompt_diff(prompt_prior, prompt_curr)
            param_diff = compute_parameter_diff(snap_prior, snap_curr)
            perf = compute_performance_delta(prior_version, v, include_template_evals=False)
            changes_summary = generate_change_summary(p_diff, param_diff, perf)
        else:
            changes_summary = ["Initial release"]

        changelog_entries.append(
            {
                "id": str(v.id),
                "version_number": v.version_number,
                "version_name": v.version_name or f"v{v.version_number}",
                "status": v.status,
                "commit_message": v.commit_message,
                "release_notes": v.release_notes,
                "score": float(v.score) if v.score is not None else None,
                "pass_rate": float(v.pass_rate) if v.pass_rate is not None else None,
                "test_count": v.test_count or 0,
                "changes_summary": changes_summary,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "is_active": v.is_active,
                "is_latest": v.is_latest,
            }
        )

    return changelog_entries
