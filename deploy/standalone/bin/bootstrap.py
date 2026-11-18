"""One-shot bootstrap of the standalone install's app container.

Runs on every container start, before the API and the collector (both wait
for /data/.bootstrap-ok). In ONE Django process it does what the distributed stack's
one-shot jobs in docker-compose.distributed.yml do, minus PeerDB:

  postgres-schema-bootstrap    createcachetable, migrate, seed_system_evals and
                               register_temporal_schedules (entrypoint.sh with
                               SERVICE_TYPE=bootstrap)
  clickhouse-native-bootstrap  oss_cdc_install --phase native --apply
  property-catalog-clickhouse-bootstrap
                               observed-attribute index database, users, grants
  oss_outbox_cdc.ensure_installed()
                               Postgres -> ClickHouse CDC as FI_CDC_MODE says:
                               trigger capture plus its drain schedules for
                               `outbox`, neither for `peerdb`/`off`

Those steps are `manage.py bootstrap_install`'s (futureagi/tfc/management/
commands/bootstrap_install.py, the Helm chart's bootstrap Job), imported and
run in the same order, so every install path prepares the datastores the
same way. This script adds only what the Standalone image needs: refusing a
Distributed install's database, skipping migrate on an unchanged image,
collectstatic, and the first-run experience below.

Database mutations are allowed only inside this process; the API and every
`docker compose exec` keep the container's NO_STARTUP_DB_MUTATIONS=true. When
the image and the applied migrations are unchanged since the last successful
boot, migrate and the seeds are skipped. ClickHouse schema, CDC and the
Temporal schedules and search attributes are applied every time: those stores
can be reset independently of Postgres.

On failure it prints why, waits 30 s and exits 1; supervisord starts it again.

First-run experience, around that work:

  * The first attempt of every boot prints a summary of the configuration:
    setup, version, URLs and which optional integrations are on (LLM
    providers by name only, email, telemetry). Never a secret's value.
  * While it runs, the API port answers every request with a JSON 503
    ("starting", and the current phase) instead of refusing connections, and
    each phase is written to /run/futureagi/status.json, which the UI's
    starting page (deploy/standalone/starting.html, served by nginx on :3000
    until the app is up) shows.
  * Once done it frees the API port, marks the boot ready for the API and the
    collector, and re-executes itself as a small waiter (no Django in memory
    while the API starts): when /health/ answers it writes
    /run/futureagi/ready, which switches nginx from the starting page to the
    app, and prints one "ready" line with the total boot time.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/app/backend"))
DATA = Path(os.environ.get("FI_DATA_DIR", "/data"))
READY = DATA / ".bootstrap-ok"
FINGERPRINT = DATA / ".bootstrap-fingerprint"
BUILD_ID = Path("/etc/futureagi/build-id")
# Written by the image build when collectstatic succeeded there.
STATIC_COLLECTED = Path("/etc/futureagi/static-collected")
RETRY_DELAY_SECONDS = 30
TRUE = ("1", "true", "yes", "on")
# Set to adopt a database created by the Distributed setup (see
# refuse_foreign_database).
ADOPT_DISTRIBUTED_INSTALL = "FI_ADOPT_DISTRIBUTED_INSTALL_DATA"
# Written after the first successful boot on an adopted database.
ADOPTED = DATA / ".adopted-distributed-install"
# The datastore steps this script runs (manage.py bootstrap_install).
BOOTSTRAP_INSTALL = "tfc.management.commands.bootstrap_install"

# Boot progress for the UI's starting page. bin/start empties this directory
# on every container start. World-readable: nginx's workers read it.
RUN_DIR = Path(os.environ.get("FI_RUN_DIR", "/run/futureagi"))
STATUS_FILE = RUN_DIR / "status.json"
# nginx serves the app instead of the starting page once this exists.
UI_READY = RUN_DIR / "ready"
SUMMARY_SHOWN = RUN_DIR / "summary-shown"
# The API's in-container port (supervisord.conf) and its health endpoint.
API_PORT = 8000
HEALTH_URL = f"http://127.0.0.1:{API_PORT}/health/"
# Epoch seconds of this container start, exported by bin/start.
BOOT_STARTED_AT = "FI_BOOT_STARTED_AT"
WAIT_FOR_API = "--wait-for-api"
# Bind-mounted from GOOGLE_APPLICATION_CREDENTIALS (/dev/null when unset).
VERTEX_KEY = Path("/etc/futureagi/secrets/vertex.json")

# What the starting page and the API port say during each phase.
PHASES = {
    "starting": "Starting services",
    "waiting": "Waiting for Postgres, ClickHouse, Redis and Temporal",
    "migrating": "Migrating the database. The first boot takes a few minutes",
    "clickhouse": "Preparing the ClickHouse schema",
    "cdc": "Connecting Postgres to ClickHouse",
    "schedules": "Registering scheduled jobs",
    "api": "Starting the API",
    "retrying": (
        f"A startup step failed; retrying in {RETRY_DELAY_SECONDS} seconds. "
        "See `docker compose logs app`"
    ),
    "ready": "Ready",
}

# LLM providers reported by name when their keys are in the environment. A
# provider counts when every variable of any one group is set.
LLM_PROVIDERS = (
    ("OpenAI", (("OPENAI_API_KEY",),)),
    ("Anthropic", (("ANTHROPIC_API_KEY",),)),
    ("Google Gemini", (("GOOGLE_API_KEY",), ("GEMINI_API_KEY",))),
    ("AWS Bedrock", (("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),)),
    ("Azure OpenAI", (("AZURE_OPENAI_API_KEY",), ("AZURE_API_KEY",))),
    ("Mistral", (("MISTRAL_API_KEY",),)),
    ("Groq", (("GROQ_API_KEY",),)),
    ("Cohere", (("COHERE_API_KEY",),)),
    ("Together AI", (("TOGETHER_API_KEY",), ("TOGETHERAI_API_KEY",))),
    ("Perplexity", (("PERPLEXITY_API_KEY",),)),
    ("DeepSeek", (("DEEPSEEK_API_KEY",),)),
    ("xAI", (("XAI_API_KEY",),)),
    ("OpenRouter", (("OPENROUTER_API_KEY",),)),
    ("Fireworks", (("FIREWORKS_API_KEY",),)),
)

_phase = "starting"
_placeholder = None


class BootstrapError(RuntimeError):
    """A failure with an actionable message; no traceback needed."""


def log(message: str) -> None:
    print(f"[bootstrap] {message}", flush=True)


def env_set(name: str) -> bool:
    """Set to a real value: not empty and not an installer placeholder."""
    value = os.environ.get(name, "").strip()
    return bool(value) and not value.upper().startswith("CHANGEME")


def write_status(phase: str) -> None:
    """Best effort: the starting page is a nicety, never a reason to fail."""
    payload = {
        "status": "ready" if phase == "ready" else "starting",
        "phase": phase,
        "message": PHASES[phase],
    }
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload) + "\n")
        tmp.chmod(0o644)
        os.replace(tmp, STATUS_FILE)
    except OSError:
        pass


def set_phase(phase: str) -> None:
    global _phase
    _phase = phase
    write_status(phase)


class _StartingHandler(BaseHTTPRequestHandler):
    """Answers every request on the API port with a JSON 503 while the
    bootstrap runs, so clients see "starting" instead of connection refused.
    Says nothing but the phase: no error text, no configuration."""

    server_version = "futureagi"
    sys_version = ""

    def _reply(self, body: bool = True) -> None:
        payload = json.dumps(
            {
                "status": "starting",
                "phase": _phase,
                "message": f"Future AGI is starting: {PHASES[_phase]}.",
                "retry_after_seconds": 5,
            }
        ).encode()
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", "5")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if body:
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
        self._reply()

    def do_HEAD(self) -> None:  # noqa: N802
        self._reply(body=False)

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_GET

    def log_message(self, *args) -> None:
        pass


class _PlaceholderServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        # A probe that hangs up before the reply (a healthcheck or curl
        # timeout) is not worth a traceback in the boot log.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def start_placeholder(port: int = API_PORT):
    """Hold the API port until the API itself binds it. None when the port
    is taken (the API is already up, e.g. after `supervisorctl restart
    bootstrap`)."""
    try:
        server = _PlaceholderServer(("0.0.0.0", port), _StartingHandler)
    except OSError:
        return None
    threading.Thread(
        target=server.serve_forever, name="starting-api", daemon=True
    ).start()
    return server


def stop_placeholder() -> None:
    """Free the API port. Before READY: the API binds it as soon as it starts."""
    global _placeholder
    if _placeholder is not None:
        _placeholder.shutdown()
        _placeholder.server_close()
        _placeholder = None


def public_url(url_var: str, port_var: str, default_port: int) -> str:
    return (os.environ.get(url_var) or "").strip().rstrip("/") or (
        f"http://localhost:{os.environ.get(port_var) or default_port}"
    )


def telemetry_config():
    """tfc.deployment_telemetry.config: the version and telemetry rules the
    backend follows. It needs no Django, so the summary can show them first."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from tfc.deployment_telemetry import config

    return config


def version() -> str:
    """The version telemetry reports: FUTURE_AGI_VERSION, else the image's."""
    value = telemetry_config().get_version()
    if value != "unknown":
        return f"version {value}"
    return "version not pinned (FUTURE_AGI_VERSION)"


def llm_providers() -> list[str]:
    names = [
        name
        for name, groups in LLM_PROVIDERS
        if any(all(env_set(var) for var in group) for group in groups)
    ]
    try:
        if VERTEX_KEY.stat().st_size > 0:
            names.append("Vertex AI")
    except OSError:
        pass
    return names


def email_delivery() -> str | None:
    """How emails go out, or None when they are only written to the log."""
    backend = (os.environ.get("EMAIL_BACKEND") or "").strip()
    # tfc.utils.email counts the console and dummy backends as undelivered.
    if backend and not any(name in backend for name in ("console", "dummy")):
        return f"EMAIL_BACKEND={backend}"
    if not backend and env_set("MAILGUN_API_KEY"):
        domain = (
            os.environ.get("MAILGUN_SENDER_DOMAIN") or "MAILGUN_SENDER_DOMAIN unset"
        )
        return f"Mailgun ({domain})"
    return None


def email_summary() -> str:
    return (
        email_delivery()
        or "off: emails are written to this log instead (MAILGUN_API_KEY sends them)"
    )


def telemetry_summary() -> str:
    """When and where tfc.deployment_telemetry sends, by its own rules; what
    it sends is the telemetry page's
    (https://docs.futureagi.com/docs/self-hosting/configuration/telemetry)."""
    config = telemetry_config()
    if config.telemetry_is_disabled():
        return (
            "off: one registration ping (instance id, version, deployment type, "
            "timestamp) only"
        )
    host = config.get_telemetry_url().split("://", 1)[-1].split("/", 1)[0]
    return (
        "on: owner, admin and staff emails once, then usage counts every "
        f"{config.get_telemetry_interval_hours()} h, never content, to {host}. "
        "FUTURE_AGI_TELEMETRY_DISABLED=true turns it off"
    )


# Hooks of Future AGI's hosted service; each sends data out when set, and is
# skipped when empty (deploy/env-reference.toml, "Integrations of Future
# AGI's hosted service").
OUTBOUND_HOOKS = (
    ("HubSpot", "HUBSPOT_API_TOKEN"),
    ("Slack", "SLACK_WEBHOOK_CHANNEL"),
    ("Slack error reports", "ERROR_LOGS_WEBHOOK"),
    ("Mixpanel", "MIX_PANEL_TOKEN"),
    ("PostHog", "POSTHOG_API_KEY"),
    ("Sentry", "SENTRY_DSN"),
)


def outbound_hooks_summary() -> str:
    names = [name for name, var in OUTBOUND_HOOKS if env_set(var)]
    return ", ".join(names) if names else "none"


def password_reset_summary(email_on: bool) -> str:
    """Mirrors accounts.views.signup: OSS_RETURN_PASSWORD_RESET_LINK=true (the
    only value it accepts) hands the reset link to whoever asks for it."""
    if (os.environ.get("OSS_RETURN_PASSWORD_RESET_LINK") or "").lower() == "true":
        return (
            "UNSAFE: the link goes to whoever asks (OSS_RETURN_PASSWORD_RESET_LINK=true), "
            "so anyone who can reach the API can take over any account. Delete it "
            "from .env unless every user of this host is trusted"
        )
    if email_on:
        return "by email"
    return (
        "no email, so an admin resets passwords: "
        "`docker compose exec app python manage.py reset_password --email <address>`"
    )


def model_serving_summary() -> str:
    url = (os.environ.get("MODEL_SERVING_URL") or "").strip()
    host = url.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
    if host:
        try:
            socket.getaddrinfo(host, None)
            return "on"
        except OSError:
            pass
    return "off (add `ml` to COMPOSE_PROFILES in .env and run `docker compose up -d`)"


def boot_summary() -> list[str]:
    """What this boot runs with, for the top of `docker compose logs app`.
    Names and URLs only, never a secret's value."""
    profiles = {
        p.strip() for p in (os.environ.get("COMPOSE_PROFILES") or "").split(",")
    }
    dev = (os.environ.get("FI_DEV_RELOAD") or "").lower() in TRUE
    providers = llm_providers()
    rows = [
        ("UI", public_url("FRONTEND_URL", "FRONTEND_PORT", 3000)),
        ("API", public_url("VITE_HOST_API", "BACKEND_PORT", 8000)),
        (
            "Traces",
            public_url("FI_COLLECTOR_PUBLIC_URL", "FI_COLLECTOR_OTLP_HTTP_PORT", 4318)
            + " (OTLP/HTTP; the SDK's FI_BASE_URL)",
        ),
        (
            "LLM keys",
            ", ".join(providers)
            if providers
            else "none in .env (add provider keys there or in the UI)",
        ),
        ("Email", email_summary()),
        ("Password reset", password_reset_summary(email_delivery() is not None)),
        ("Telemetry", telemetry_summary()),
        ("Other hooks", outbound_hooks_summary()),
        (
            "Code evals",
            "nsjail sandbox container (COMPOSE_PROFILES=sandbox)"
            if "sandbox" in profiles
            else "built-in sandbox",
        ),
        ("Model serving", model_serving_summary()),
        (
            "Edition",
            "Enterprise (EE_LICENSE_KEY)"
            if env_set("EE_LICENSE_KEY")
            else "Open source",
        ),
    ]
    width = max(len(label) for label, _ in rows)
    setup = "Standalone setup" + (" (dev: hot reload)" if dev else "")
    return (
        [f"Future AGI · {setup} · {version()}"]
        + [f"  {label.ljust(width)}  {value}" for label, value in rows]
        + [
            "  Every setting: https://docs.futureagi.com/docs/self-hosting/configuration/reference"
        ]
    )


def show_summary_once() -> None:
    """On the first attempt of each boot only; a retry after a failure
    does not repeat it."""
    if SUMMARY_SHOWN.exists():
        return
    try:
        for line in boot_summary():
            log(line)
    except Exception as exc:  # never block a boot on its own summary
        log(f"(configuration summary unavailable: {type(exc).__name__})")
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        SUMMARY_SHOWN.touch()
    except OSError:
        pass


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def boot_started_at(fallback: float) -> float:
    try:
        return float(os.environ[BOOT_STARTED_AT])
    except (KeyError, ValueError):
        return fallback


def api_healthy(opener) -> bool:
    try:
        with opener.open(HEALTH_URL, timeout=3) as response:
            return response.status == 200
    except (OSError, ValueError):
        # URLError and HTTPError (a 503 while it starts) are OSErrors.
        return False


def wait_for_api(started_at: float, poll_seconds: float = 1.0) -> int:
    """The waiter: runs after the bootstrap re-executes itself, without
    Django. Switches the UI from the starting page to the app once the API
    answers, and prints the one line an operator waits for."""
    set_phase("api")
    # No proxy: HTTP(S)_PROXY in .env must not route a loopback probe.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    waiting_since = time.monotonic()
    next_note = 60.0
    while not api_healthy(opener):
        waited = time.monotonic() - waiting_since
        if waited >= next_note:
            log(
                f"still waiting for the API to answer {HEALTH_URL} after "
                f"{format_duration(waited)}; its errors are above in this log"
            )
            next_note = waited + 300
        time.sleep(poll_seconds)
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        UI_READY.touch()
        UI_READY.chmod(0o644)
    except OSError as exc:
        log(
            f"could not switch the UI to the app ({exc}); it keeps showing the starting page"
        )
    set_phase("ready")
    log(
        f"Future AGI is ready in {format_duration(time.time() - started_at)}. "
        f"Open {public_url('FRONTEND_URL', 'FRONTEND_PORT', 3000)}"
    )
    return 0


def hand_over(started_at: float) -> None:
    """Re-execute as the waiter, so the bootstrap's Django process does not
    sit in memory next to the starting API. In-process if exec fails."""
    try:
        from django.db import connections

        connections.close_all()
    except Exception:
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execv(
            sys.executable,
            [sys.executable, os.path.abspath(__file__), WAIT_FOR_API, str(started_at)],
        )
    except OSError as exc:
        log(f"re-exec failed ({exc}); waiting for the API in this process")
    sys.exit(wait_for_api(started_at))


def configure_django() -> None:
    """This process alone may mutate the databases. The backend is importable
    from here on; main() sets Django up once the datastores answer."""
    os.environ["NO_STARTUP_DB_MUTATIONS"] = "false"
    os.environ["SERVICE_TYPE"] = "bootstrap"
    # Django's historical 0078 migration must never replay the ClickHouse SQL glob.
    os.environ.setdefault("FI_SKIP_CH25_MIGRATION", "1")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
    sys.path.insert(0, str(PROJECT_ROOT))
    os.chdir(PROJECT_ROOT)


def refuse_foreign_database() -> bool:
    """Stop before adopting a Postgres database this install did not create.

    A distributed install keeps uploads in its own MinIO volume and Temporal state in
    Postgres, so running the standalone install on its database would silently
    orphan both. A database created by a Postgres build with a different C
    library (e.g. a Debian-based image read by an Alpine one) sorts text
    differently, and its indexes would return wrong results.

    Returns True while a distributed install's database is being adopted
    (FI_ADOPT_DISTRIBUTED_INSTALL_DATA). Once an adopted boot has succeeded, the
    Temporal databases the distributed stack left behind no longer count, so the
    flag can be removed; PeerDB slots always do (the CDC takeover drops them).
    """
    from django.db import connection

    reasons = []
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_replication_slots WHERE slot_name LIKE %s",
            ["peerflow%"],
        )
        if cursor.fetchone()[0]:
            reasons.append("it has PeerDB replication slots")
        cursor.execute(
            "SELECT count(*) FROM pg_database WHERE datname IN (%s, %s)",
            ["temporal", "temporal_visibility"],
        )
        if cursor.fetchone()[0]:
            if ADOPTED.exists():
                log(
                    "the distributed install's Temporal databases are unused; drop them "
                    "when convenient (DROP DATABASE temporal_visibility, and "
                    "temporal if present)"
                )
            else:
                reasons.append("the Postgres server holds Temporal's databases")
        cursor.execute("SELECT current_setting('server_version_num')::int")
        if cursor.fetchone()[0] >= 150000:
            cursor.execute(
                "SELECT datcollversion, pg_database_collation_actual_version(oid) "
                "FROM pg_database WHERE datname = current_database()"
            )
            recorded, actual = cursor.fetchone()
            if recorded is not None and actual is None:
                raise BootstrapError(
                    f"the database was created with glibc collation {recorded}, "
                    "but this Postgres server uses another C library, which "
                    "sorts text differently. Run it with the postgres image "
                    "that created the volume (postgres:16)."
                )
    if not reasons:
        return False
    if os.environ.get(ADOPT_DISTRIBUTED_INSTALL, "").lower() in TRUE:
        log(f"adopting a distributed install's database ({'; '.join(reasons)})")
        return True
    raise BootstrapError(
        "this Postgres database belongs to a Distributed install "
        f"(docker-compose.distributed.yml): {'; '.join(reasons)}. Keep running it "
        "with the Distributed setup: set COMPOSE_FILE=docker-compose.distributed.yml in .env "
        "(./bin/install does this for an existing Distributed install), then "
        "`docker compose up -d`. Moving an install's data between the Distributed "
        "and the Standalone setup is not supported; to start over with the "
        "Standalone setup, back up, run ./bin/uninstall --wipe-data, then "
        f"./bin/install. ({ADOPT_DISTRIBUTED_INSTALL}=true on the app service forces "
        "the move: uploads in the old MinIO volume and in-flight workflows "
        "are left behind.)"
    )


def schema_fingerprint() -> tuple[str, list[str]]:
    """Hash of what decides whether migrate and the seeds must run."""
    from django.apps import apps

    migrations = sorted(
        f"{config.label}/{path.stem}"
        for config in apps.get_app_configs()
        for path in Path(config.path, "migrations").glob("[0-9]*.py")
    )
    inputs = {
        "version": os.environ.get("FUTURE_AGI_VERSION", ""),
        "build": BUILD_ID.read_text() if BUILD_ID.exists() else "",
        "ee": bool(os.environ.get("EE_LICENSE_KEY")),
        "cloud": os.environ.get("CLOUD_DEPLOYMENT", ""),
        "migrations": migrations,
    }
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return digest, migrations


def unapplied_migrations(migrations: list[str]) -> set[str]:
    """On-disk migrations the database has not recorded (all when it is new)."""
    from django.db import connection

    if "django_migrations" not in connection.introspection.table_names():
        return set(migrations)
    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations")
        applied = {f"{app}/{name}" for app, name in cursor.fetchall()}
    return set(migrations) - applied


def migrate_and_seed() -> str:
    """bootstrap_install's migrate and seeds, skipped while the image and the
    applied migrations are unchanged since the last successful boot."""
    import tfc.management.commands.bootstrap_install as steps

    fingerprint, migrations = schema_fingerprint()
    stored = FINGERPRINT.read_text().strip() if FINGERPRINT.exists() else None
    if stored == fingerprint and not unapplied_migrations(migrations):
        log(
            "image and migrations unchanged since the last boot; skipping migrate and seeds"
        )
        return fingerprint
    set_phase("migrating")
    steps.migrate_and_seed(log)
    return fingerprint


def collect_static() -> None:
    """Done at image build (which writes STATIC_COLLECTED); here only when
    that step failed, and then once per container."""
    import tfc.management.commands.bootstrap_install as steps

    if STATIC_COLLECTED.exists():
        return
    try:
        steps.call("collectstatic", log, interactive=False, verbosity=0)
    except Exception as exc:  # non-fatal, as in entrypoint.sh
        log(f"collectstatic failed (continuing): {exc}")
        return
    try:
        STATIC_COLLECTED.touch()
    except OSError:
        pass


def main() -> None:
    READY.unlink(missing_ok=True)
    started = time.monotonic()
    set_phase("waiting")
    configure_django()
    # A plain import: `from package import module` turns a missing module
    # into an ImportError ("cannot import name") without its name.
    try:
        import tfc.management.commands.bootstrap_install as steps
    except ModuleNotFoundError as exc:
        if exc.name != BOOTSTRAP_INSTALL:
            raise
        raise BootstrapError(
            "this backend image predates the Standalone setup: it has no "
            "`manage.py bootstrap_install`, whose steps this bootstrap runs. Build "
            "the image on a backend from the same checkout (./bin/install "
            "--from-source does) or on a release that ships it."
        ) from None

    for name, host, port in steps.datastore_endpoints():
        steps.wait_tcp(name, host, port, 300, log)

    import django

    django.setup()
    adopting = refuse_foreign_database()
    fingerprint = migrate_and_seed()
    set_phase("clickhouse")
    steps.clickhouse_native_schema(log, 600)
    steps.property_catalog(log)
    set_phase("schedules")
    steps.with_retries(
        "Temporal search attributes",
        lambda: steps.register_search_attributes(log),
        log,
    )
    set_phase("cdc")
    steps.change_data_capture(log, attempts=12, delay=5)
    set_phase("schedules")
    steps.with_retries(
        "Temporal schedules",
        lambda: steps.call("register_temporal_schedules", log),
        log,
    )
    collect_static()

    FINGERPRINT.write_text(fingerprint + "\n")
    if adopting:
        ADOPTED.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ\n", time.gmtime()))
    # The API binds the port the moment READY lets it start.
    stop_placeholder()
    set_phase("api")
    READY.touch()
    log(
        f"bootstrap done in {format_duration(time.monotonic() - started)}; starting the API"
    )


if __name__ == "__main__":
    if sys.argv[1:2] == [WAIT_FOR_API]:
        try:
            started_at = float(sys.argv[2])
        except (IndexError, ValueError):
            started_at = time.time()
        sys.exit(wait_for_api(boot_started_at(started_at)))

    # The shared steps fail with bootstrap_install.BootstrapError, a
    # CommandError; like this script's own, its message says what to do.
    from django.core.management.base import CommandError

    attempt_started_at = time.time()
    show_summary_once()
    _placeholder = start_placeholder()
    try:
        main()
    except (BootstrapError, CommandError) as exc:
        log(f"FAILED: {exc}")
    except (Exception, SystemExit):
        traceback.print_exc()
    else:
        hand_over(boot_started_at(attempt_started_at))
    # The placeholder keeps answering "starting" through the back-off.
    set_phase("retrying")
    log(f"retrying in {RETRY_DELAY_SECONDS}s")
    time.sleep(RETRY_DELAY_SECONDS)
    sys.exit(1)
