import os

import structlog
from django.apps import AppConfig
from django.db.models.signals import post_migrate

from tfc.ee_loader import is_cloud_env

logger = structlog.get_logger(__name__)

STARTUP_SAFE_MANAGEMENT_COMMANDS = frozenset(
    {
        "check",
        "collectstatic",
        # First-account bootstrap and account recovery that self-hosted
        # installs tell an operator to run ("Forgot password" without email
        # names reset_password); each writes only the account it is given,
        # and AppConfig.ready stays mutation-free.
        "changepassword",
        "create_user",
        "createsuperuser",
        "reset_password",
        "generate_swagger",
        "grpcrunaioserver",
        "runserver",
        # Read the migration table and the migration files; write nothing.
        "showmigrations",
        "sqlmigrate",
        "start_temporal_worker",
    }
)

HOSTED_ENV_TYPES = frozenset({"prod", "production", "staging"})

# Application processes are never schema/bootstrap runners. A one-shot operator
# job may run one of these explicit management commands, but it does not enable
# mutation hooks during AppConfig initialization.
OPERATOR_STARTUP_MUTATION_COMMANDS = frozenset(
    {
        "ch25_apply_schema",
        "ch25_remove_pg",
        "backfill_legacy_scans",
        "backfill_score_tracer_project",
        # Helm bootstrap Job: migrate, seeds, ClickHouse schema, CDC, schedules.
        "bootstrap_install",
        "createcachetable",
        "drop_legacy_observation_span",
        "migrate",
        "provision_grouping_features",
        "register_temporal_schedules",
        "seed_system_evals",
    }
)
# Developer tools that only the explicit local mode (./bin/dev manage) may run:
# makemigrations writes migration files into the checkout, and shell runs
# arbitrary code. Hosted processes and operator jobs never get them.
LOCAL_DEVELOPER_COMMANDS = frozenset({"makemigrations", "shell"})
OPERATOR_STARTUP_MUTATION_MODE = "operator"
OPERATOR_STARTUP_SERVICE_TYPE = "bootstrap"

# Temporal schedules are not database state, so every deploy may sync them
# without the operator's database authority. The registrar may run only the
# bare registration: no option can delete or narrow the schedule set, so a
# schedule this release does not define is never touched.
SCHEDULE_REGISTRAR_SERVICE_TYPE = "temporal-schedules"
SCHEDULE_REGISTRATION_COMMAND = "register_temporal_schedules"


def startup_db_mutations_disabled() -> bool:
    """Return whether implicit AppConfig database mutations are disabled.

    Every Django initialization is mutation-free, including local development
    and processes whose deployment environment is absent or incomplete.
    Database bootstrap is an explicit one-shot management-command workflow,
    never a side effect of importing Django in a web, worker, shell, or
    diagnostic process.

    ``NO_STARTUP_DB_MUTATIONS`` remains a validated compatibility input, but a
    value of ``false`` can no longer re-enable the retired ``AppConfig.ready``
    mutation hooks. Explicit operator commands are authorized independently.
    """

    value = os.getenv("NO_STARTUP_DB_MUTATIONS")
    if value is not None and value not in {"true", "false"}:
        raise RuntimeError("NO_STARTUP_DB_MUTATIONS must be exactly 'true' or 'false'")
    return True


def hosted_startup_environment() -> bool:
    """Return whether this process belongs to a hosted deployment."""

    return (
        os.getenv("ENV_TYPE", "").strip().lower() in HOSTED_ENV_TYPES or is_cloud_env()
    )


def _management_command(argv: list[str]) -> str | None:
    """Extract a Django management command from a process argv."""

    if not argv:
        return None

    executable_path = os.path.normpath(argv[0]).replace("\\", "/")
    executable = os.path.basename(executable_path)
    path_parts = tuple(part for part in executable_path.split("/") if part)
    is_django_module_main = path_parts[-2:] == ("django", "__main__.py")
    if executable == "manage.py" and len(argv) >= 2:
        return argv[1]
    if executable in {"django-admin", "django-admin.py"} and len(argv) >= 2:
        return argv[1]
    if is_django_module_main and len(argv) >= 2:
        # CPython rewrites ``python -m django`` to django/__main__.py before
        # Django initializes the application registry.
        return argv[1]
    if (
        executable.startswith("python")
        and len(argv) >= 4
        and argv[1:3] == ["-m", "django"]
    ):
        return argv[3]
    return None


def operator_startup_mutation_authorized(argv: list[str]) -> bool:
    """Authorize one allowlisted command in a dedicated operator job.

    The two explicit environment values keep ordinary backend/worker pods and
    ad-hoc ``manage.py shell`` sessions out of the mutation path. The entrypoint
    also enforces this contract before executing any bootstrap command.
    """

    mode = os.getenv("STARTUP_DB_MUTATION_MODE", "disabled")
    if mode not in {"disabled", OPERATOR_STARTUP_MUTATION_MODE}:
        raise RuntimeError(
            "STARTUP_DB_MUTATION_MODE must be exactly 'disabled' or 'operator'"
        )
    return (
        mode == OPERATOR_STARTUP_MUTATION_MODE
        and os.getenv("SERVICE_TYPE") == OPERATOR_STARTUP_SERVICE_TYPE
        and _management_command(argv) in OPERATOR_STARTUP_MUTATION_COMMANDS
    )


def schedule_registration_authorized(argv: list[str]) -> bool:
    """Authorize the one-shot schedule registrar's closed command line."""

    command = _management_command(argv)
    return (
        os.getenv("SERVICE_TYPE") == SCHEDULE_REGISTRAR_SERVICE_TYPE
        and command == SCHEDULE_REGISTRATION_COMMAND
        and not argv[argv.index(command) + 1 :]
    )


def explicit_management_mutation_authorized(argv: list[str]) -> bool:
    """Authorize one allowlisted explicit command, never an AppConfig hook.

    Hosted deployments require the dedicated operator/bootstrap pair, except
    the schedule registrar's bare registration. Local and self-hosted
    entrypoints preserve their documented migration workflow only when they
    explicitly export ``NO_STARTUP_DB_MUTATIONS=false``; that explicit local
    mode also runs the ``LOCAL_DEVELOPER_COMMANDS``.
    """

    command = _management_command(argv)
    explicit_local_mode = (
        os.getenv("NO_STARTUP_DB_MUTATIONS") == "false"
        and not hosted_startup_environment()
    )
    if command in LOCAL_DEVELOPER_COMMANDS:
        return explicit_local_mode
    if command not in OPERATOR_STARTUP_MUTATION_COMMANDS:
        return False
    if operator_startup_mutation_authorized(argv) or schedule_registration_authorized(
        argv
    ):
        return True
    return explicit_local_mode


def guarded_management_command(argv: list[str]) -> str | None:
    """Return a Django command forbidden during mutation-free startup."""

    command = _management_command(argv)
    if command is None or command in STARTUP_SAFE_MANAGEMENT_COMMANDS:
        return None
    if command == "migrate":
        # Django 5.1 runs --prune before the --check exit. Permit only this
        # closed check-only form, not arbitrary options containing --check.
        options = argv[argv.index(command) + 1 :]
        if "--check" in options and set(options) <= {
            "--check",
            "--noinput",
            "--no-input",
        }:
            return None
    if command == "makemigrations":
        # The review check: it reports missing or conflicting migrations and
        # writes no file. Only this closed form, as for migrate --check.
        options = set(argv[argv.index(command) + 1 :])
        required = {"--check", "--dry-run"}
        if required <= options <= required | {"--noinput", "--no-input"}:
            return None
    return command


def _seed_prompt_labels_after_migrate(sender, **kwargs):
    """Auto-seed the global Production/Staging/Development prompt labels
    after every migrate (TH-7261).

    Nothing else ever creates these rows — the only other caller is an API
    action the frontend never hits — so a fresh database shows "No labels
    found" in the Add Tags modal. Mirrors the node-template seeding in
    agent_playground.apps. Idempotent via get_or_create.
    """
    try:
        from model_hub.models.prompt_label import PromptLabel

        created = PromptLabel.create_default_system_labels()
        if created:
            logger.info(
                "default_prompt_labels_seeded",
                created=[label.name for label in created],
            )
    except Exception:
        logger.exception("default_prompt_label_seed_failed")


class ModelHubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "model_hub"

    def ready(self):
        # Import signal handlers, then enforce the mutation-free startup
        # contract before any application process can serve work.
        # Never mutate a database while the application registry initializes.
        import sys

        import model_hub.signals  # noqa: F401

        startup_db_mutations_disabled()
        if command := guarded_management_command(sys.argv):
            if not explicit_management_mutation_authorized(sys.argv):
                raise RuntimeError(
                    f"{command} is disabled during mutation-free startup. "
                    f"Only {sorted(OPERATOR_STARTUP_MUTATION_COMMANDS)} may run "
                    "in a one-shot SERVICE_TYPE=bootstrap process with "
                    "STARTUP_DB_MUTATION_MODE=operator, or via the explicit "
                    "local migration mode (NO_STARTUP_DB_MUTATIONS=false outside "
                    "a hosted deployment, as ./bin/dev manage runs), which also "
                    f"allows {sorted(LOCAL_DEVELOPER_COMMANDS)}"
                )
            if command == "migrate":
                post_migrate.connect(
                    _seed_prompt_labels_after_migrate,
                    sender=self,
                    dispatch_uid="model_hub_seed_default_prompt_labels",
                )
        logger.info("Mutation-free startup; no implicit seed or schema setup")


##################################################
##################################################
##################################################

# types of model input
# numerical data: continuous , Discrete Data
# Categorical Data
# Text Data
# Natural Language: Human language text (e.g., articles, social media posts).
# Structured Text: Data that comes in a structured format like JSON or XML.
# Unstructured Text: Free-form text without any specific forma
# Image Data
# Audio Data
# Video Data
# Time-Series Data:
# Sequential Data: Data points indexed in time order (e.g., stock prices, weather data).
# Event Log Data: Timestamped logs of events (e.g., web logs, transaction logs).
# Geospatial Data:
# Coordinate Data: Latitude and longitude points.
# Map Data: Data used in mapping and geographical information systems (GIS).
# Sensor Data:

# IoT Data: Data from Internet of Things devices.
# Biometric Data: Fingerprints, facial recognition data.
# Graph Data:

# Networks: Data representing nodes and connections (e.g., social networks, neural networks).
# Trees: Hierarchical data structures.
# Complex Data Structures:

# Hierarchical Data: Data in tree-like structures.
# Mixed Data Types: Combinations of different types (e.g., a dataset with images, text, and numerical values).
# Synthetic / Artificial Data:

# Generated Data: Data generated through simulations or algorithms to model real-world phenomena.
# Encoded Data:

# One-Hot Encoding: Representing categorical data as binary vectors.
# Feature Vectors: Numerically encoded features of complex data (e.g., text, images).
# Sequence Data:

# DNA Sequences: Genetic data.
# Instruction Sequences: Step-by-step instructions or commands.

##################################################
##################################################
##################################################

# supported ai tasks

# text to speech
# ner
# classification
# regression
# image all
# lllm all
