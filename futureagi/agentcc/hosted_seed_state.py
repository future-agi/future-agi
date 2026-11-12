"""Read-only verification of the installed post_migrate baseline.

Receivers own their seeds. Never duplicate seeding here: a failed check requires
an explicit backfill apply, which re-emits Django's standard post_migrate signal.
"""

from django.contrib.auth import get_permission_codename
from django.core.management.base import CommandError


def verify_seed_state(apps, using):
    content_type = apps.get_model("contenttypes", "ContentType")
    permission = apps.get_model("auth", "Permission")
    content_types = set(
        content_type.objects.using(using).values_list("app_label", "model")
    )
    permissions = set(
        permission.objects.using(using).values_list(
            "content_type__app_label", "content_type__model", "codename"
        )
    )
    for model in apps.get_models():
        opts = model._meta
        identity = (opts.app_label, opts.model_name)
        if identity not in content_types:
            raise CommandError("Hosted post_migrate content types are incomplete")
        expected = {
            get_permission_codename(action, opts) for action in opts.default_permissions
        } | {codename for codename, _ in opts.permissions}
        if any((*identity, codename) not in permissions for codename in expected):
            raise CommandError("Hosted post_migrate permissions are incomplete")
    # EE is optional; inspect only the installed migration-state registry. The
    # choices are the seed contract, not a second hand-maintained list of tiers.
    if any(config.label == "usage" for config in apps.get_app_configs()):
        tier = apps.get_model("usage", "SubscriptionTier")
        required = {name for name, _ in tier._meta.get_field("name").choices}
        installed = set(tier.objects.using(using).values_list("name", flat=True))
        if not required or not required <= installed:
            raise CommandError("Hosted post_migrate subscription tiers are incomplete")
