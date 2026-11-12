"""Normalize hosted two-key org periods without changing any pushed period.

c2.6 twin, a bridge rather than a replacement: a c2.5.x database recorded this
migration as prism 0081_normalize_two_key_org_period, so on it the database step
is skipped (SkipDatabaseIfLegacyApplied) and only the new name is recorded.

A legacy orgPeriod can be shadowed by a nested org entry when pushed, yet win
when a writer normalizes aliases. Keep the push's period in org_period and
remove only the shadowed alias. Use the same pure transform as the hosted
period lock, including its stamped-entry precedence and hierarchy defaults.

Write a new version under the organization lock, one transaction per org.
Every other concrete value is copied, without decrypting or re-stamping it.
The old budgets stay in history; reverse is a no-op. Nothing is pushed here:
the next push or bulk sync picks up the new version/id metadata with the same
budget payload. Hosted backfill runs this after the schema phase, quiesced.
Self-hosted installs are untouched.
"""

from copy import deepcopy

from django.db import DEFAULT_DB_ALIAS, migrations, transaction
from django.db.models import Max

from tfc.ee_loader import is_cloud_env
from tfc.migration_bridge import SkipDatabaseIfLegacyApplied

DESCRIPTION = "Normalize legacy orgPeriod (period unchanged)"


def _replacement_budgets(budgets):
    """Return (replacement or None, skipped), never mutate the stored value."""
    # Delay live contract imports until RunPython, not graph construction.
    from agentcc.contracts.gateway_admin import BudgetsConfig
    from agentcc.services.budget_periods import (
        changes_existing_budget_period,
        effective_budget_periods,
    )
    from agentcc.services.config_push import _latest_stamped_org_budget_entry

    if (
        not isinstance(budgets, dict)
        or not budgets.get("orgPeriod")
        or budgets.get("org_period")
        or not any(
            isinstance(budgets.get(key), dict)
            for key in ("organization", "org", "hard_limit")
        )
        or _latest_stamped_org_budget_entry(budgets) is not None
    ):
        return None, False

    normalized = BudgetsConfig._normalize_input_aliases(budgets)
    if not changes_existing_budget_period(budgets, normalized):
        return None, False
    before = effective_budget_periods(budgets)
    period = before.get(("org", ""))
    if period is None or period == effective_budget_periods(normalized).get(
        ("org", "")
    ):
        return None, False

    replacement = deepcopy(budgets)
    replacement.pop("orgPeriod")
    replacement["org_period"] = period
    # Never fix the org period at the expense of another counter identity.
    if effective_budget_periods(replacement) != before:
        return None, True
    return replacement, False


def _rewrite_active(apps, using, config_id, org_id):
    Organization = apps.get_model("accounts", "Organization")
    OrgConfig = apps.get_model("prism", "AgentccOrgConfig")
    configs = OrgConfig._base_manager.db_manager(using)
    with transaction.atomic(using=using):
        # Match the shared writer's organization lock before reading versions.
        list(
            Organization._base_manager.db_manager(using)
            .select_for_update(no_key=True)
            .filter(pk=org_id)
            .only("pk")
        )
        active = (
            configs.select_for_update()
            .filter(pk=config_id, organization_id=org_id, is_active=True, deleted=False)
            .first()
        )
        if active is None:
            return False, False
        try:
            replacement, skipped = _replacement_budgets(active.budgets)
        except (TypeError, ValueError):
            # Malformed legacy budgets cannot establish the invariant. Count
            # them without exposing row identifiers, values or validation text.
            return False, True
        if replacement is None:
            return False, skipped

        version = (
            configs.filter(organization_id=org_id).aggregate(last=Max("version"))[
                "last"
            ]
            + 1
        )
        fields = {
            field.attname: deepcopy(getattr(active, field.attname))
            for field in OrgConfig._meta.concrete_fields
            if not field.primary_key
            and field.name
            not in {"version", "is_active", "created_at", "updated_at", "deleted_at"}
        }
        fields.update(
            version=version,
            is_active=True,
            budgets=replacement,
            change_description=DESCRIPTION,
        )
        # The partial unique active-row constraint requires this order. If the
        # insert fails, this transaction restores the previous active row.
        configs.filter(pk=active.pk).update(is_active=False)
        configs.create(**fields)
    return True, False


def forwards(apps, schema_editor):
    changed = organizations = skipped = 0
    if is_cloud_env():
        using = (
            schema_editor.connection.alias
            if schema_editor is not None
            else DEFAULT_DB_ALIAS
        )
        OrgConfig = apps.get_model("prism", "AgentccOrgConfig")
        candidates = (
            OrgConfig._base_manager.db_manager(using)
            .filter(is_active=True, deleted=False, budgets__has_key="orgPeriod")
            .order_by("pk")
            .values_list("pk", "organization_id")
        )
        for config_id, org_id in candidates.iterator(chunk_size=500):
            wrote, unsafe = _rewrite_active(apps, using, config_id, org_id)
            changed += int(wrote)
            # The active-row constraint allows at most one candidate per org.
            organizations += int(wrote)
            skipped += int(unsafe)
    if skipped:
        print(changed, organizations, skipped)
    else:
        print(changed, organizations)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("prism", "0088_mcp_shared_connect_generation")]
    # c2.5.x recorded this as prism.0081_normalize_two_key_org_period.
    operations = [
        SkipDatabaseIfLegacyApplied(
            legacy=("prism", "0081_normalize_two_key_org_period"),
            operations=[migrations.RunPython(forwards, migrations.RunPython.noop)],
        )
    ]
