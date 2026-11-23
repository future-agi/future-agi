"""Single deployment rollout switch for derived metrics."""

from django.conf import settings


def metrics_enabled(org=None, project=None) -> bool:
    """Return the rollout decision for an organization or project.

    The first rollout is deployment-wide: org/project do not grant an override.
    Keep scope at this boundary so callers do not invent their own flag checks.
    This is independent of licensing and never replaces workspace permissions.
    """
    return getattr(settings, "METRICS_ENABLED", 0) == 1
