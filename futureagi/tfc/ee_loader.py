import importlib.util
import os


def has_ee(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        return False


# Where the Temporal worker loads Future AGI Cloud's usage hooks from, the
# legacy EE location last (tfc/temporal/common/registry.py). They ship the
# UsageConsumerWorkflow, the consumer of the usage:events Redis stream.
USAGE_TEMPORAL_MODULES = ("ee.cloud.temporal", "ee.usage.temporal")


def usage_event_consumer_available() -> bool:
    """Whether this code ships the consumer of the usage:events Redis stream."""
    return any(has_ee(module) for module in USAGE_TEMPORAL_MODULES)


# The CLOUD_DEPLOYMENT values of Future AGI Cloud's regions.
CLOUD_DEPLOYMENTS = ("US", "EU", "DEV")


def is_cloud_env(deployment: str | None = None) -> bool:
    """Whether CLOUD_DEPLOYMENT names a Future AGI Cloud region, in any case
    and with surrounding whitespace ignored.

    ``deployment`` defaults to the environment variable; runtime code passes
    ``settings.CLOUD_DEPLOYMENT``. Any other value, "false" included, means a
    self-hosted install.
    """
    if deployment is None:
        deployment = os.environ.get("CLOUD_DEPLOYMENT", "")
    return deployment.strip().upper() in CLOUD_DEPLOYMENTS


def _is_oss_mode() -> bool:
    """Env-var-based OSS detection for use during Django settings load.

    Mirrors ee.usage.deployment.DeploymentMode precedence but doesn't go
    through django.conf.settings (which isn't fully populated while
    settings.py is still executing). Runtime code should use
    DeploymentMode.is_oss() instead — this is for the app-registration gate.
    """
    if is_cloud_env():
        return False
    if os.environ.get("EE_LICENSE_KEY", ""):
        return False
    return True


def ee_feature_enabled(module: str) -> bool:
    """True iff an EE feature module should be wired up.

    Requires both:
      - module is importable (code present on disk), and
      - deployment is not OSS (env vars indicate EE or Cloud).

    `ee.usage` itself is exempt — it provides DeploymentMode, so it's gated
    on presence only via `has_ee("ee.usage")`.
    """
    return has_ee(module) and not _is_oss_mode()
