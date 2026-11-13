"""DEBUG stays off unless it is set explicitly, whatever ENV_TYPE says."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = """
import json
import sys
import types
import django
from django.conf import settings
from django.urls import path

# Use the real tfc settings module and Django's request/exception handling,
# with a single failing view and no application or service setup.
urls = types.ModuleType("debug_default_probe_urls")
def fail(request):
    raise RuntimeError("debug-default-probe-marker")
urls.urlpatterns = [path("debug-default-probe/", fail)]
sys.modules[urls.__name__] = urls
settings.ROOT_URLCONF = urls.__name__
settings.INSTALLED_APPS = []
settings.MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
settings.TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": False, "OPTIONS": {}}]
settings.LOGGING_CONFIG = None
settings.LOGGING = {}
django.setup()
from django.test import Client
client = Client(raise_request_exception=False)
response = client.get("/debug-default-probe/", HTTP_HOST="api.debug-probe.example")
body = response.content.decode()
print(json.dumps({"debug": settings.DEBUG, "status": response.status_code,
    "exception_in_body": "debug-default-probe-marker" in body,
    "technical_page": "Traceback" in body,
    "hosts": settings.ALLOWED_HOSTS}))
"""


def render_error_with_settings(env_type=None, debug=None):
    env = {
        key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ
    }
    env.update(
        PYTHONPATH=str(ROOT),
        DJANGO_SETTINGS_MODULE="tfc.settings.settings",
        SECRET_KEY="synthetic-debug-probe-only-do-not-deploy-0123456789",
        BASE_URL="https://api.debug-probe.example",
        ALLOWED_HOSTS="api.debug-probe.example",
        APP_URL="https://app.debug-probe.example",
        FUTURE_AGI_TELEMETRY_ENABLED="false",
        FUTURE_AGI_TELEMETRY_DISABLED="true",
        FUTURE_AGI_TELEMETRY_URL="http://127.0.0.1:9",
        FUTURE_AGI_LICENSE_URL="http://127.0.0.1:9",
        SENTRY_ENABLED="false",
        NO_STARTUP_DB_MUTATIONS="true",
    )
    if env_type is not None:
        env["ENV_TYPE"] = env_type
    if debug is not None:
        env["DEBUG"] = debug
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.splitlines()[-1])


@pytest.mark.parametrize(
    "env_type", [None, "local", "test", "dev", "prod", "production"]
)
def test_unset_debug_defaults_to_plain_error_page(env_type):
    result = render_error_with_settings(env_type=env_type)
    assert result["status"] == 500
    assert result["debug"] is False, result
    assert result["exception_in_body"] is False
    assert result["technical_page"] is False
    assert "*" not in result["hosts"]
    assert "api.debug-probe.example" in result["hosts"]


@pytest.mark.parametrize("env_type", ["local", "test", "dev", "prod"])
def test_explicit_debug_true_shows_technical_error_page(env_type):
    result = render_error_with_settings(env_type=env_type, debug="true")
    assert result["status"] == 500
    assert result["debug"] is True
    assert result["exception_in_body"] is True
    assert result["technical_page"] is True


@pytest.mark.parametrize("env_type", [None, "local", "test", "dev", "prod"])
def test_explicit_debug_false_is_respected(env_type):
    result = render_error_with_settings(env_type=env_type, debug="false")
    assert result["status"] == 500
    assert result["debug"] is False, result
    assert result["exception_in_body"] is False
    assert result["technical_page"] is False
