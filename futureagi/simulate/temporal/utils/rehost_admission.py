"""Admission before recording downloads and storage, never just before billing."""

import importlib.util


class RecordingRehostRefused(RuntimeError):
    """A typed refusal retained in the converter's structured failure log."""

    def __init__(self, error_code: str, reason: str):
        super().__init__(reason)
        self.error_code = error_code


def require_recording_rehost_admission(
    project_id: str | None, *, amount: int = 0, call_execution_id: str | None = None
) -> None:
    """Check the canonical project or call's organization afresh for each action.

    OSS is explicitly nonbillable. An absent tenant, broken EE import, missing
    store or failed check in a billable deployment is never an implicit allow.
    Call from a sync thread; the async converter delegates this DB/Redis work.
    """
    try:
        try:
            from ee.usage.deployment import DeploymentMode
        except ImportError:
            if importlib.util.find_spec("ee") is None:
                return
            raise

        if DeploymentMode.is_oss():
            return

        from ee.usage.services.metering import check_usage

        if project_id:
            from tracer.models.project import Project

            # Background activities have no request workspace, and simulation
            # projects are billable too. Include both project kinds.
            org_id = (
                Project.with_simulation.filter(id=project_id, deleted=False)
                .values_list("organization_id", flat=True)
                .first()
            )
            event_type = "observe_add"
        elif call_execution_id:
            from simulate.models.test_execution import CallExecution

            # A simulation need not have an observability provider/project.
            # Resolve its tenant from the owning run, never an arbitrary org hint.
            org_id = (
                CallExecution.all_objects.filter(
                    id=call_execution_id,
                    deleted=False,
                    test_execution__deleted=False,
                    test_execution__run_test__deleted=False,
                )
                .values_list("test_execution__run_test__organization_id", flat=True)
                .first()
            )
            event_type = "voice_recording_storage"
        else:
            raise ValueError("Recording rehost requires a project or owning call")
        if not org_id:
            raise ValueError("Recording organization is unavailable")
        check = check_usage(str(org_id), event_type, amount=amount)
        if not check.allowed:
            raise RecordingRehostRefused(check.error_code, check.reason)
    except RecordingRehostRefused:
        raise
    except Exception:
        raise RecordingRehostRefused(
            "BUDGET_CHECK_UNAVAILABLE",
            "Recording storage admission is unavailable.",
        ) from None
