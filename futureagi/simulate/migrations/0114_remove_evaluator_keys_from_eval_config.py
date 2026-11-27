"""Remove the evaluator settings that grading leaked into simulate eval attachments.

Self-contained on purpose: a historical migration must not change when live code does,
so the key lists and the rule are a frozen copy of ``simulate.services.eval_config_repair``
and nothing from ``simulate`` is imported. The command ``strip_eval_config_evaluator_keys``
runs the live rule again after a rollout and can restore what was copied here.

Cost: one sequential pass over ``simulate_eval_config`` in primary-key pages of 500, and
one short transaction per changed row, so no lock is held across the table.
"""

import structlog
from django.db import migrations, transaction

logger = structlog.get_logger(__name__)

# Every key the evaluator set-up wrote as of this migration; `code` is a real input.
EVALUATOR_WRITTEN_KEYS = frozenset(
    {
        "api_key",
        "provider",
        "model",
        "model_type",
        "rule_prompt",
        "system_prompt",
        "output_type",
        "messages",
        "few_shot_examples",
        "check_internet",
        "multi_choice",
        "choices",
        "choice_scores",
        "pass_threshold",
        "reverse_output",
        "knowledge_base_id",
        "agent_mode",
        "tools",
        "knowledge_bases",
        "data_injection",
        "summary",
        "organization_id",
        "workspace_id",
        "custom_eval",
        "param_modalities",
        "required_keys",
        "template_format",
    }
)
# A nested dict holding both keys of any pair was graded with the leak.
LEAK_SIGNAL_PAIRS = (
    ("pass_threshold", "model"),
    ("model", "output_type"),
    ("model", "provider"),
)
PAGE_SIZE = 500


def _has_leak_signal(nested):
    return any(a in nested and b in nested for a, b in LEAK_SIGNAL_PAIRS)


def _keys_to_remove(nested, template_config):
    removed = set()
    if "api_key" in nested:
        removed.add("api_key")
    if _has_leak_signal(nested):
        parameters = (
            template_config.get("config") if isinstance(template_config, dict) else None
        )
        kept = frozenset(parameters) if isinstance(parameters, dict) else frozenset()
        removed.update(
            k for k in nested if k in EVALUATOR_WRITTEN_KEYS and k not in kept
        )
    return sorted(removed)


def _copyable(removed):
    return {k: v for k, v in removed.items() if k != "api_key" and v is not None}


def _read_page(model, after_pk, alias):
    rows = model._base_manager.using(alias).filter(config__has_key="config")
    if after_pk is not None:
        rows = rows.filter(pk__gt=after_pk)
    return list(
        rows.order_by("pk")
        .select_related("eval_template")
        .only("id", "config", "eval_template__config")[:PAGE_SIZE]
    )


def forwards(apps, schema_editor):
    alias = schema_editor.connection.alias
    model = apps.get_model("simulate", "SimulateEvalConfig")
    removed_model = apps.get_model("simulate", "SimulateEvalConfigRemovedKeys")
    scanned = 0
    changed = 0
    last_pk = None
    while True:
        page = _read_page(model, last_pk, alias)
        for row in page:
            if not isinstance(row.config, dict):
                continue
            nested = row.config.get("config")
            if not isinstance(nested, dict):
                continue
            scanned += 1
            removed = _keys_to_remove(nested, row.eval_template.config)
            if not removed:
                continue
            cleaned = {k: v for k, v in nested.items() if k not in removed}
            copy = _copyable({k: nested[k] for k in removed})
            with transaction.atomic(using=alias):
                # Only if nobody saved the row meanwhile; the command picks it up later.
                written = (
                    model._base_manager.using(alias)
                    .filter(pk=row.pk, config=row.config)
                    .update(config={**row.config, "config": cleaned})
                )
                if written and copy:
                    removed_model.objects.using(alias).create(
                        eval_config_id=row.pk, removed=copy
                    )
            changed += written
        if len(page) < PAGE_SIZE:
            break
        last_pk = page[-1].pk
    logger.info(
        "simulate_eval_config_evaluator_keys_stripped",
        scanned=scanned,
        changed=changed,
    )


UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:model_hub_evaltemplate",
        "postgres:simulate_eval_config",
        "postgres:simulate_eval_config_removed_keys",
    ],
    "mitigation": (
        "One pass over simulate_eval_config in primary-key pages of 500, reading "
        "each page with its template through select_related; atomic=False, so no "
        "lock is held across the table. Each changed row is written in its own "
        "short transaction with a conditional update (only if config still equals "
        "what was read), and what it removes, except the provider key and empty "
        "values, is copied in the same transaction to the empty table "
        "simulate_eval_config_removed_keys, created by 0113 with no foreign key. "
        "model_hub_evaltemplate is only read. Rows without the leak signal lose "
        "only the provider key. A run cut off halfway is safe to resume: cleaned "
        "rows show no signal and make no second copy. simulate_eval_config is not "
        "a registered high-volume table, so the normal bootstrap rehearsal applies. "
        "Before release, run the read-only counts on a replica and check the run "
        "time against the bootstrap job deadline; if it comes near that deadline, "
        "run a reference-scale rehearsal first. The command "
        "strip_eval_config_evaluator_keys repeats the clean-up after rollout, and "
        "--restore puts copied values back."
    ),
    "reviewer": "cdileep23",
    "review": "https://github.com/future-agi/future-agi/pull/3389#discussion_r4597642757",
    "large_volume_rehearsal": True,
}


class Migration(migrations.Migration):
    # Each row is written in its own short transaction, so no lock is held over the
    # whole table and an interrupted run keeps the rows it already cleaned.
    atomic = False

    dependencies = [
        ("simulate", "0113_simulate_eval_config_removed_keys"),
    ]

    operations = [
        # The removed values are kept in simulate_eval_config_removed_keys; the
        # strip_eval_config_evaluator_keys command restores them.
        migrations.RunPython(forwards, migrations.RunPython.noop, atomic=False),
    ]
