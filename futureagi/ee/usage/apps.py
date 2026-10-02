import structlog
from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = structlog.get_logger(__name__)


class UsageConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # Python path to this package. App was relocated to ee/ for OSS-readiness.
    name = "ee.usage"
    # Pin the Django app label to "usage" so the django_migrations table,
    # usage_* table names, and FKs from other apps keep resolving after the move.
    label = "usage"

    def ready(self):
        # Connect the post_migrate signal to create usage entries after migrations
        # This ensures tables exist before we try to insert data
        post_migrate.connect(self.create_usage_entries_after_migrate, sender=self)

    def create_usage_entries_after_migrate(self, sender, **kwargs):
        """
        Create usage entries after migrations are complete.
        This ensures tables exist before we try to insert data.
        """
        from ee.usage.utils.usage_entries import create_usage_entries

        try:
            create_usage_entries()
            logger.info("Usage entries created successfully after migration")
        except Exception as e:
            logger.exception(
                f"Failed to create usage entries after migration: {str(e)}"
            )

    # def update_stripe_keys(self):
    #     print("--------------------------------------------")
    #     print("update_stripe_keys")
    #     print("--------------------------------------------")
    #     from tfc.settings import STRIPE_TEST_SECRET_KEY, STRIPE_LIVE_SECRET_KEY
    #     from djstripe.models import APIKey
    #     try:
    #         if APIKey.objects.filter(name="test").exists():
    #             APIKey.objects.filter(name="test").update(secret=STRIPE_TEST_SECRET_KEY, livemode=False)
    #         else:
    #             APIKey.objects.create(name="test", secret=STRIPE_TEST_SECRET_KEY, livemode=False)
    #         print("test key updated")
    #         if APIKey.objects.filter(name="live").exists():
    #             APIKey.objects.filter(name="live").update(secret=STRIPE_LIVE_SECRET_KEY, livemode=True)
    #         else:
    #             APIKey.objects.create(name="live", secret=STRIPE_LIVE_SECRET_KEY, livemode=True)
    #         print("live key updated")
    #     except Exception as e:
    #         print(e)

    # def create_subscription_tier_and_limits(self):
    #     print("--------------------------------------------")
    #     print("create_subscription_tier_and_limits")
    #     print("--------------------------------------------")
    #     from ee.usage.utils.usage_entries import (
    #         insert_subscription_tier_entries,
    #         insert_api_call_type_entries,
    #         insert_resource_type_entries,
    #         insert_rate_limit_entries,
    #         insert_resource_limit_entries,
    #         insert_pricing_entries,
    #         subscription_tier_entries,
    #         api_call_type_entries,
    #         resource_type_choices,
    #         rate_limit_entries,
    #         resource_limit_entries,
    #         pricing_entries
    #     )
    #     insert_subscription_tier_entries(subscription_tier_entries)
    #     insert_api_call_type_entries(api_call_type_entries)
    #     insert_resource_type_entries(resource_type_choices)
    #     insert_rate_limit_entries(rate_limit_entries)
    #     insert_resource_limit_entries(resource_limit_entries)
    #     insert_pricing_entries(pricing_entries)
