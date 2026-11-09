"""Django app for the Omega Hub registry (SPEC §0.1, §14.1)."""

from django.apps import AppConfig
from django.db.models.signals import post_migrate


class OmegaHubConfig(AppConfig):
    name = "omega_hub"
    label = "omega_hub"
    verbose_name = "Omega Hub"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Imported for their registrations: the maintainer-grant receiver, the Hub's system checks and
        # the erasure receivers on account and organisation deletion (HUB-09).
        from omega_hub import signals, system_checks  # noqa: F401
        from omega_hub.services import erasure  # noqa: F401
        from omega_hub.services.storage import check_storage_config
        from omega_hub.services.storage_provision import provision_after_migrate

        check_storage_config()
        # The deploy-wiring checks (W004-W008, ops_checks.py) and the outbox's account-erasure
        # receiver (outbox_erasure.py) register on import.
        import omega_hub.ops_checks  # noqa: F401
        import omega_hub.outbox_erasure  # noqa: F401

        # Connecting writes nothing; the receiver runs only after an explicit, authorized
        # `migrate` (every deploy shape's bootstrap step) and provisions the Hub bucket there.
        post_migrate.connect(provision_after_migrate, sender=self, dispatch_uid="omega_hub_storage_provision")
