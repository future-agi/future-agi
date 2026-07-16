# Test settings for Django Backend
import os
import sys
import tempfile
import traceback
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Force EE mode during pytest runs so EE feature code (falcon_ai, etc.) is
# registered in INSTALLED_APPS and its test modules can import models cleanly.
# The verify-ee-*.sh scripts still exercise OSS mode explicitly via subprocess
# env toggling; this only affects the pytest process.
os.environ.setdefault("EE_LICENSE_KEY", "test-license-key")

from .settings import *  # noqa: F403,E402
from .settings import INSTALLED_APPS  # noqa: E402

# Test mode indicator
TESTING = True
DEBUG = False

# Test database configuration
# Use different ports than dev (5432/9000) to avoid collisions
# Dev: PG=5432, CH=9000 | Test: PG=15432, CH=19000
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PG_DB", "test_tfc"),
        "USER": os.environ.get("PG_USER", "test_user"),
        "PASSWORD": os.environ.get("PG_PASSWORD", "test_password"),
        "HOST": os.environ.get("PG_HOST", "localhost"),
        "PORT": os.environ.get("PG_PORT", "15432"),
        "TEST": {
            "NAME": os.environ.get("PG_TEST_DB", "test_tfc_test"),
        },
    }
}

CLICKHOUSE = {
    "CH_HOST": os.environ.get("CH_HOST", "localhost"),
    "CH_PORT": os.environ.get("CH_PORT", "19000"),
    "CH_USERNAME": os.environ.get("CH_USERNAME", "default"),
    "CH_PASSWORD": os.environ.get("CH_PASSWORD", ""),
    "CH_DATABASE": os.environ.get("CH_DATABASE", "test_tfc"),
    "CH_ENABLED": os.environ.get("CH_ENABLED", "true").lower() in ("true", "1", "yes"),
}

# Point Redis-using code at the test compose sidecar at localhost:16379
# (per docker-compose.test.yml). Without this, modules fall through to the
# dev `.env` host `redis://redis:6379/0` which doesn't resolve outside
# Docker, and the payload_storage / distributed_locks helpers raise
# "Redis is not available" mid-test.
os.environ.setdefault("REDIS_URL", "redis://localhost:16379/0")
os.environ.setdefault("REDIS_LOCK_URL", "redis://localhost:16379/2")

# CHSpanReader / CH25 v2 service uses clickhouse-connect over HTTP. The TCP
# port lives in `CLICKHOUSE['CH_PORT']` (19000) but the HTTP listener is on
# 18123 in the test compose. Without this dict `get_v2_config()` would fall
# back to the hardcoded HTTP default 8123 and hit the dev CH 24.10 instead.
CLICKHOUSE_V2 = {
    "CH25_HOST": os.environ.get("CH25_HOST", "localhost"),
    "CH25_HTTP_PORT": int(os.environ.get("CH25_HTTP_PORT", "18123")),
    "CH25_TCP_PORT": int(os.environ.get("CH25_TCP_PORT", "19000")),
    "CH25_USER": os.environ.get("CH25_USER", "default"),
    "CH25_PASSWORD": os.environ.get("CH25_PASSWORD", ""),
    "CH25_DATABASE": os.environ.get("CH25_DATABASE", "test_tfc"),
    "QUERY_TYPES_V2_PRIMARY": os.environ.get(
        "CH25_QUERY_TYPES_V2_PRIMARY", "dashboard"
    ),
    "QUERY_TYPES_V2_ONLY": os.environ.get("CH25_QUERY_TYPES_V2_ONLY", ""),
    "QUERY_TYPES_SHADOW": os.environ.get("CH25_QUERY_TYPES_SHADOW", ""),
    "QUERY_TYPES_DISABLED": os.environ.get("CH25_QUERY_TYPES_DISABLED", ""),
}

CH25_EVAL_LOGGER_TABLE = os.environ.get(
    "CH25_EVAL_LOGGER_TABLE", "tracer_eval_logger_v2"
)

# Test cache configuration
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}

# Test Celery configuration
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "memory://")
CELERY_RESULT_BACKEND = "cache+memory://"

# Test file storage
DEFAULT_FILE_STORAGE = "django.core.files.storage.InMemoryStorage"
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"

# Test MinIO configuration
MINIO_URL = os.environ.get("MINIO_URL", "test-minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "test_user")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "test_password")
MINIO_USE_HTTPS = False


# Disable migrations for faster tests
class DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


# MIGRATION_MODULES = DisableMigrations()

# Email backend for testing
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Password hashers for faster tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Logging configuration for tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
        },
        "django.db.backends": {
            "handlers": ["console"],
            "level": "WARNING",
        },
    },
}

# Test-specific middleware (remove some for faster tests)
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

# Disable some apps for faster tests
INSTALLED_APPS = [app for app in INSTALLED_APPS if "debug_toolbar" not in app]

# Test-specific settings
SECRET_KEY = "test-secret-key-for-testing-only"
ALLOWED_HOSTS = ["*"]
INTEGRATION_ENCRYPTION_KEY = os.environ.get(
    "INTEGRATION_ENCRYPTION_KEY",
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
)

# Security settings for tests
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

# Disable external services for tests
USE_TZ = True
TIME_ZONE = "UTC"

# API settings for tests
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("accounts.authentication.APIKeyAuthentication",),
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_PARSER_CLASSES": (
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
    ),
    "DEFAULT_PAGINATION_CLASS": "tfc.utils.pagination.ExtendedPageNumberPagination",
    "PAGE_SIZE": 10,  # Number of objects per page.
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "EXCEPTION_HANDLER": "accounts.authentication.custom_exception_handler",
}


# Test media files
MEDIA_URL = "/test-media/"
MEDIA_ROOT = os.path.join(tempfile.gettempdir(), "test-media")

# Test static files
STATIC_URL = "/test-static/"
STATIC_ROOT = os.path.join(tempfile.gettempdir(), "test-static")

# Model serving URL for integration tests
MODEL_SERVING_URL = os.environ.get(
    "MODEL_SERVING_URL", "http://test-model-serving:8080"
)

# Disable analytics and tracking in tests
MIXPANEL_TOKEN = None
SENTRY_DSN = None

# Test-specific feature flags
FEATURE_FLAGS = {
    "enable_new_ui": False,
    "enable_analytics": False,
    "enable_monitoring": False,
}


def ensure_clickhouse_test_database():
    """Ensure the ClickHouse test database exists"""
    try:
        from clickhouse_driver import Client

        # Connect to ClickHouse without specifying a database
        client = Client(
            host=CLICKHOUSE["CH_HOST"],
            port=int(CLICKHOUSE["CH_PORT"] or "9000"),
            user=CLICKHOUSE["CH_USERNAME"],
            password=CLICKHOUSE["CH_PASSWORD"],
        )

        # Create the test database if it doesn't exist
        database_name = CLICKHOUSE["CH_DATABASE"]
        client.execute(f"CREATE DATABASE IF NOT EXISTS {database_name}")

        print(f"✅ ClickHouse test database '{database_name}' is ready")

    except Exception as e:
        print(f"⚠️  Warning: Could not setup ClickHouse test database: {e}")
        traceback.print_exc()
        print("   Tests that require ClickHouse may fail")


# Only try to create the database if we're actually testing (not during collectstatic, etc.)
if os.environ.get("TESTING") == "true":
    ensure_clickhouse_test_database()

print("🧪 Test settings loaded successfully")
