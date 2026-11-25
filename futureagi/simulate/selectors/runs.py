"""The runs a user sees and acts on.

A fix's check runs and a coding agent's verify runs are ordinary runs of the
environment's run test that only the platform reads. Every user-facing list, count,
comparison and action starts from these selectors, so none of them reaches such a
run. Internal paths (ingestion, teardown, cleanup, billing, the fix and the verify
read) query the models directly and keep them.
"""

from django.db.models import Q, QuerySet

from simulate.models import HostedHarnessJob, TestExecution


def user_executions() -> QuerySet[TestExecution]:
    """Every TestExecution a user may see or act on. Narrow it with ``filter``."""
    return TestExecution.objects.filter(user_runs("hosted_harness_job__"))


def user_environment_runs(environment: HostedHarnessJob) -> QuerySet[HostedHarnessJob]:
    """The environment's Runs a user may see. Tenancy is the caller's: it resolved
    ``environment`` already."""
    return HostedHarnessJob.no_workspace_objects.filter(
        user_runs(), environment=environment
    )


def user_runs(path: str = "") -> Q:
    """The same rule for an aggregate over a join, where a queryset cannot go.
    ``path`` leads from the queried model to the run's HostedHarnessJob (for example
    ``simulation_runs__`` from an environment). Anything else uses the selectors
    above.
    """
    return Q(
        **{
            f"{path}fix_run__isnull": True,
            f"{path}verify_of__isnull": True,
            # An evidence import's drafting job holds a chat turn only, never a run.
            f"{path}evidence_drafting__isnull": True,
        }
    )
