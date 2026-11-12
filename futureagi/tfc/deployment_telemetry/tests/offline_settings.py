"""SQLite-only settings for the telemetry opt-out regression suite.

Run with -c /dev/null --noconftest
--ds=tfc.deployment_telemetry.tests.offline_settings. No application startup,
Postgres, ClickHouse, Redis, Temporal server, or operator .env is needed.
"""

SECRET_KEY = "telemetry-offline-tests-only"
INSTALLED_APPS = ["tfc.deployment_telemetry"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
