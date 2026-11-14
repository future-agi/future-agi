from concurrent.futures import ThreadPoolExecutor, as_completed

import structlog
from django.db import close_old_connections

from model_hub.models.choices import StatusType
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.views.develop_optimiser import DevelopOptimizer
from tfc.telemetry import wrap_for_thread
from tfc.temporal import temporal_activity

logger = structlog.get_logger(__name__)


@temporal_activity(time_limit=3600, queue="tasks_l")
def optimization_runner():
    # Get all not started optimization tasks
    optimizations = OptimizationDataset.objects.filter(
        status=StatusType.NOT_STARTED.value
    ).all()

    # Wrap function with OTel context propagation for thread safety
    wrapped_process_optimization = wrap_for_thread(process_optimization)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(wrapped_process_optimization, optimization)
            for optimization in optimizations
        ]

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"An error occurred: {e}")


def process_optimization(optimization):
    try:
        close_old_connections()
        # Take the row out of NOT_STARTED before working on it. A pass that
        # outlives its time limit keeps draining its own list in its thread
        # while the next pass lists the rows it has not reached yet; only the
        # pass whose update moves the row runs it.
        claimed = OptimizationDataset.objects.filter(
            id=optimization.id, status=StatusType.NOT_STARTED.value
        ).update(status=StatusType.RUNNING.value)
        if not claimed:
            return
        try:
            optimizer = DevelopOptimizer(optim_obj_id=optimization.id)
            optimizer.run()
        except Exception:
            # The error may be the connection itself going away, and the
            # write below runs on this thread's connection: a dead one is
            # dropped first so the write gets a fresh one.
            close_old_connections()
            # A claimed row that fails before or while it runs would read as
            # running for good: nothing picks a Running row up again. A row
            # the optimizer has ended itself (completed, or failed with its
            # reason stored) is no longer Running and is left as it is.
            try:
                OptimizationDataset.objects.filter(
                    id=optimization.id, status=StatusType.RUNNING.value
                ).update(status=StatusType.FAILED.value)
            except Exception:
                # The row stays Running. The error that reaches the caller
                # must still be the one that stopped the run, not this one,
                # so this one is recorded here with its traceback: nothing
                # else will ever see it.
                logger.error(
                    "optimization_mark_failed_error",
                    optimization_id=str(optimization.id),
                    exc_info=True,
                )
            raise
    finally:
        close_old_connections()
