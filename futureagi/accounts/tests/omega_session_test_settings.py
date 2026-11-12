"""Disposable SQLite request-boundary checks; no host credentials or services."""

SECRET_KEY = "disposable-omega-issuer-test-only"
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "accounts"]
AUTH_USER_MODEL = "accounts.User"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
MIGRATION_MODULES = {"accounts": None}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "accounts.tests.omega_session_test_urls"
MIDDLEWARE = []
USE_TZ = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "accounts.authentication.custom_exception_handler",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SWAGGER_SETTINGS = {
    "DEFAULT_AUTO_SCHEMA_CLASS": "tfc.utils.api_contracts.ManagementAPIAutoSchema",
}
