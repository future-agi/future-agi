from django.db import close_old_connections

from model_hub.services.pairwise_judges import dispatch_job, execute_item
from tfc.routers import primary_required
from tfc.temporal import temporal_activity


@temporal_activity(time_limit=3600, queue="tasks_s")
@primary_required
def dispatch_pairwise_job(job_id: str):
    try:
        close_old_connections()
        dispatch_job(job_id)
    finally:
        close_old_connections()


@temporal_activity(time_limit=600, queue="tasks_s")
@primary_required
def evaluate_pairwise_item(item_id: str):
    try:
        close_old_connections()
        execute_item(item_id)
    finally:
        close_old_connections()
