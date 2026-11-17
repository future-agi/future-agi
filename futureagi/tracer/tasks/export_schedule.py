from tfc.temporal import temporal_activity
from tracer.services.export_schedule import dispatch_due_exports


@temporal_activity(max_retries=0, time_limit=300, queue="tasks_s")
def dispatch_export_schedules():
    """Periodic dispatcher; the existing tasks_l worker performs each export."""
    return dispatch_due_exports()
