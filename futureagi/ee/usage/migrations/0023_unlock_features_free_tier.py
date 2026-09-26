from django.db import migrations


_BOOL_FEATURES = (
    "has_knowledge_base",
    "has_review_workflow",
    "has_agreement_metrics",
    "has_required_labels",
    "has_audit_logs",
    "has_voice_sim",
    "has_synthetic_data",
    "has_agentic_eval",
    "has_optimization",
    "has_scim",
    "has_project_rbac",
    "has_custom_roles",
    "has_data_masking",
)

_INT_FEATURES = {
    "knowledge_bases": -1,
}

_PLANS = ("free", "payg", "boost", "scale", "enterprise", "custom")

_PRIOR_BOOL_DEFAULTS = {
    "has_knowledge_base":   {"free": False, "payg": False, "boost": True,  "scale": True,  "enterprise": True, "custom": True},
    "has_review_workflow":  {"free": False, "payg": False, "boost": True,  "scale": True,  "enterprise": True, "custom": True},
    "has_agreement_metrics": {"free": False, "payg": False, "boost": True, "scale": True,  "enterprise": True, "custom": True},
    "has_required_labels":  {"free": False, "payg": False, "boost": True,  "scale": True,  "enterprise": True, "custom": True},
    "has_audit_logs":       {"free": False, "payg": False, "boost": True,  "scale": True,  "enterprise": True, "custom": True},
    "has_voice_sim":        {"free": False, "payg": True,  "boost": True,  "scale": True,  "enterprise": True, "custom": True},
    "has_synthetic_data":   {"free": False, "payg": False, "boost": True,  "scale": True,  "enterprise": True, "custom": True},
    "has_agentic_eval":     {"free": False, "payg": False, "boost": True,  "scale": True,  "enterprise": True, "custom": True},
    "has_optimization":     {"free": False, "payg": False, "boost": False, "scale": True,  "enterprise": True, "custom": True},
    "has_scim":             {"free": False, "payg": False, "boost": False, "scale": True,  "enterprise": True, "custom": True},
    "has_project_rbac":     {"free": False, "payg": False, "boost": True,  "scale": True,  "enterprise": True, "custom": True},
    "has_custom_roles":     {"free": False, "payg": False, "boost": False, "scale": True,  "enterprise": True, "custom": True},
    "has_data_masking":     {"free": False, "payg": False, "boost": False, "scale": False, "enterprise": True, "custom": True},
}

_PRIOR_INT_DEFAULTS = {
    "knowledge_bases": {"free": 0, "payg": 0, "boost": 5, "scale": -1, "enterprise": -1, "custom": -1},
}


def _bust_redis_cache(features):
    try:
        from ee.usage.services.emitter import get_redis

        r = get_redis()
        for feature in features:
            try:
                for key in r.scan_iter(match=f"ent:*:{feature}"):
                    r.delete(key)
            except Exception:
                continue
    except Exception:
        pass


def _apply(apps, schema_editor):
    PlanEntitlement = apps.get_model("usage", "PlanEntitlement")

    for feature in _BOOL_FEATURES:
        for plan in _PLANS:
            PlanEntitlement.objects.update_or_create(
                feature=feature,
                plan=plan,
                organization=None,
                defaults={"value_int": None, "value_bool": True, "deleted": False},
            )

    for feature, value in _INT_FEATURES.items():
        for plan in _PLANS:
            PlanEntitlement.objects.update_or_create(
                feature=feature,
                plan=plan,
                organization=None,
                defaults={"value_int": value, "value_bool": None, "deleted": False},
            )

    _bust_redis_cache(list(_BOOL_FEATURES) + list(_INT_FEATURES.keys()))


def _revert(apps, schema_editor):
    PlanEntitlement = apps.get_model("usage", "PlanEntitlement")

    for feature, plan_map in _PRIOR_BOOL_DEFAULTS.items():
        for plan, value in plan_map.items():
            PlanEntitlement.objects.update_or_create(
                feature=feature,
                plan=plan,
                organization=None,
                defaults={"value_int": None, "value_bool": value, "deleted": False},
            )

    for feature, plan_map in _PRIOR_INT_DEFAULTS.items():
        for plan, value in plan_map.items():
            PlanEntitlement.objects.update_or_create(
                feature=feature,
                plan=plan,
                organization=None,
                defaults={"value_int": value, "value_bool": None, "deleted": False},
            )

    _bust_redis_cache(list(_PRIOR_BOOL_DEFAULTS.keys()) + list(_PRIOR_INT_DEFAULTS.keys()))


class Migration(migrations.Migration):

    dependencies = [
        ("usage", "0022_merge_20260417_1114"),
    ]

    operations = [
        migrations.RunPython(_apply, _revert),
    ]
