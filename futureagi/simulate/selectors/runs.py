"""Reads shared by every list of an environment's runs."""

from django.db.models import Q


def user_runs(path: str = "") -> Q:
    """Leaves out the check runs a fix started: they are not the user's runs, so run
    lists, counts and comparisons skip them. ``path`` leads from the queried model to
    the run's HostedHarnessJob (for example ``hosted_harness_job__`` on TestExecution).
    """
    return Q(**{f"{path}fix_run__isnull": True})
