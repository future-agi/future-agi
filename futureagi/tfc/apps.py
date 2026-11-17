from django.apps import AppConfig


class TfcConfig(AppConfig):
    name = "tfc"
    verbose_name = "TFC"

    def ready(self):
        """
        Called when Django is fully loaded.

        This is the proper place to import signal handlers.
        """
        # Import signals to register them
        # This must be done here, not in __init__.py, to avoid
        # importing Django before it's configured.
        from tfc.utils import signals  # noqa: F401

        self._check_billing_catalog_flag()

    @staticmethod
    def _check_billing_catalog_flag():
        """Refuse to boot with BILLING_CATALOG_ALLOW_UNCONFIRMED set beside a
        live Stripe key, STRIPE_MODE=live or a production ENV_TYPE (the
        BL-LEDGER-2A interface: ``rating.check_unconfirmed_flag()`` at boot).
        A build without the ledger app has no catalog to guard."""
        from django.apps import apps

        if not apps.is_installed("ee.usage"):
            return
        from ee.usage.services import rating

        rating.check_unconfirmed_flag()
