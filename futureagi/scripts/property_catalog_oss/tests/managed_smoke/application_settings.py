"""Normal OSS settings with only isolated test infrastructure integrations."""

import os
from pathlib import Path

from tfc.settings.settings import *  # noqa: F403

_run = Path(os.environ["MANAGED_SMOKE_DIRECTORY"]).resolve(strict=True)
if not str(BASE_DIR).startswith(str(_run / "backend") + os.sep):  # noqa: F405
    raise RuntimeError("application settings must load a new disposable code snapshot")

# No URL checks or unrelated cache/email/worker infrastructure is necessary for
# migrations and the real management command. Catalog validation is unchanged.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MEDIA_ROOT = str(_run / "media")
STATIC_ROOT = str(_run / "static")
ROOT_URLCONF = "application_urls"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    # Retain errors/warnings during sustained real-query tests without filling
    # the bounded process log with repetitive query INFO records.
    "loggers": {"tracer.services.clickhouse.client": {"level": "WARNING"}},
}
