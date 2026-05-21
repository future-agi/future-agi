"""Backfill EvalTemplateVersion rows that were seeded empty.

Copies prompt-bearing fields from the live EvalTemplate into each
empty version row. User-owned, non-deleted, non-composite templates
only. Strictly additive (never overwrites filled rows) and idempotent.
Reverse is a no-op — rollback via DB point-in-time restore if needed.
"""

from django.db import migrations


_SKIP_EVAL_TYPES = frozenset({"CompositeEvaluator", "Composite"})


def _is_composite(template, version) -> bool:
    cs_eval_type = (version.config_snapshot or {}).get("eval_type_id")
    if cs_eval_type and cs_eval_type in _SKIP_EVAL_TYPES:
        return True
    if (template.eval_type or "") == "composite":
        return True
    cfg_type = (template.config or {}).get("eval_type_id")
    if cfg_type and cfg_type in _SKIP_EVAL_TYPES:
        return True
    if (getattr(template, "composite_child_axis", "") or "").strip():
        return True
    return False


def _version_is_empty(version) -> bool:
    if version.prompt_messages:
        return False
    if (version.criteria or "").strip():
        return False
    cs = version.config_snapshot or {}
    for key in ("rule_prompt", "instructions", "code"):
        val = cs.get(key)
        if isinstance(val, str) and val.strip():
            return False
    msgs = cs.get("messages")
    if isinstance(msgs, list) and len(msgs) > 0:
        return False
    return True


def _live_has_content(template) -> bool:
    if (template.criteria or "").strip():
        return True
    cfg = template.config or {}
    for key in ("rule_prompt", "instructions", "code"):
        val = cfg.get(key)
        if isinstance(val, str) and val.strip():
            return True
    msgs = cfg.get("messages")
    if isinstance(msgs, list) and len(msgs) > 0:
        return True
    return False


def _apply_live_to_version(version, template) -> None:
    cfg = template.config or {}

    if (template.criteria or "").strip():
        version.criteria = template.criteria

    msgs = cfg.get("messages")
    if isinstance(msgs, list) and len(msgs) > 0:
        version.prompt_messages = msgs

    snapshot = dict(version.config_snapshot or {})
    for key in ("rule_prompt", "instructions", "code", "language"):
        val = cfg.get(key)
        if isinstance(val, str) and val.strip():
            snapshot[key] = val
    if isinstance(msgs, list) and len(msgs) > 0:
        snapshot["messages"] = msgs
    version.config_snapshot = snapshot


def backfill(apps, schema_editor):
    EvalTemplateVersion = apps.get_model("model_hub", "EvalTemplateVersion")

    qs = (
        EvalTemplateVersion.objects.select_related("eval_template")
        .filter(
            eval_template__owner="user",
            eval_template__deleted=False,
        )
        .order_by("eval_template_id", "version_number")
    )

    updated = 0
    skipped_filled = 0
    skipped_composite = 0
    unrecoverable = 0

    for version in qs.iterator(chunk_size=500):
        template = version.eval_template

        if _is_composite(template, version):
            skipped_composite += 1
            continue

        if not _version_is_empty(version):
            skipped_filled += 1
            continue

        if not _live_has_content(template):
            unrecoverable += 1
            continue

        _apply_live_to_version(version, template)
        version.save(
            update_fields=[
                "prompt_messages",
                "criteria",
                "config_snapshot",
                "updated_at",
            ]
        )
        updated += 1

    print(
        f"[0099] EvalTemplateVersion backfill — "
        f"updated={updated} already_filled={skipped_filled} "
        f"composite_skipped={skipped_composite} unrecoverable={unrecoverable}"
    )


def reverse(apps, schema_editor):
    # No-op. See module docstring.
    return


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0098_merge_20260513_1258"),
    ]

    operations = [
        migrations.RunPython(backfill, reverse, elidable=False),
    ]
