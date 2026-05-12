"""TH-4909: backfill silent custom_eval_configs.

A buggy bulk-attach path persisted CustomEvalConfig rows with `config={}` for
AgentEvaluator templates. Those rows never produce eval_logger rows because
dispatch needs `output` and `rule_prompt`. This migration walks the affected
rows and merges the linked template's config into them.

Rows whose linked template ALSO has an empty config are reported (logged) but
not touched — those need manual attention from product/data.
"""
from copy import deepcopy

from django.db import migrations

_PASSTHROUGH_KEYS = (
    "output",
    "rule_prompt",
    "eval_type_id",
    "required_keys",
    "requiredKeys",
    "optional_keys",
    "optionalKeys",
    "template_format",
    "pass_threshold",
    "choice_scores",
    "config_params_desc",
    "configParamsDesc",
    "param_modalities",
    "paramModalities",
)


def _is_empty(value):
    return value in (None, "", [], {})


def backfill(apps, schema_editor):
    CustomEvalConfig = apps.get_model("tracer", "CustomEvalConfig")

    healed = 0
    skipped_template_empty = []
    skipped_not_agent = 0

    for cec in CustomEvalConfig.objects.select_related("eval_template").filter(deleted=False).iterator():
        template = cec.eval_template
        if template is None:
            continue
        if getattr(template, "eval_type", None) != "agent":
            skipped_not_agent += 1
            continue

        cec_config = cec.config or {}
        template_config = template.config or {}

        missing = [k for k in ("output", "rule_prompt") if _is_empty(cec_config.get(k))]
        if not missing:
            continue

        # If template is also empty there's nothing to copy from.
        if all(_is_empty(template_config.get(k)) for k in missing):
            skipped_template_empty.append((str(cec.id), cec.name, str(template.id), template.name))
            continue

        merged = dict(cec_config)
        for key in _PASSTHROUGH_KEYS:
            if _is_empty(merged.get(key)) and not _is_empty(template_config.get(key)):
                merged[key] = deepcopy(template_config[key])

        cec.config = merged
        cec.save(update_fields=["config", "updated_at"] if hasattr(cec, "updated_at") else ["config"])
        healed += 1

    print(f"[TH-4909] backfill complete. healed={healed} skipped_template_empty={len(skipped_template_empty)} skipped_not_agent={skipped_not_agent}")
    if skipped_template_empty:
        print("[TH-4909] These CECs need manual attention (template config is also empty):")
        for cec_id, cec_name, tpl_id, tpl_name in skipped_template_empty[:50]:
            print(f"  cec={cec_id} ({cec_name})  template={tpl_id} ({tpl_name})")


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0075_evallogger_target_type_evallogger_trace_session_and_more"),
    ]
    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
