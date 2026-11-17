"""Durable remote scorer jobs; the broker receives only the job ID."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from integrations.services.credentials import CredentialManager
from model_hub.models.remote_eval import RemoteEvalJob
from model_hub.services.remote_eval import TASK_HARD_LIMIT, TASK_SOFT_LIMIT, call_scorer, mapped_inputs, validate_task_budget
from model_hub.services.remote_eval_runs import persist_child, selected_run
from tfc.routers import force_primary


@shared_task(soft_time_limit=TASK_SOFT_LIMIT, time_limit=TASK_HARD_LIMIT)
@force_primary()
def score_remote_run(job_id):
    # Duplicate deliveries never call the endpoint twice. Calls are not replayed
    # automatically after a worker loss because remote scorers may bill per call.
    with transaction.atomic():
        job = RemoteEvalJob.objects.select_for_update().select_related("template").filter(id=job_id).first()
        if job is None or job.status != "queued":
            return
        job.status = "calling"
        job.save(update_fields=["status", "updated_at"])
    try:
        template = job.template
        if template.deleted:
            raise ValueError("Template was deleted")
        _, items = selected_run(template.organization_id, template.workspace_id, job.run_id, job.row_ids)
        remote = job.definition["remote"]
        validate_task_budget(remote, len(items))
        credentials = CredentialManager.decrypt(bytes(job.encrypted_credentials))

        def score(item):
            try:
                result = call_scorer(
                    remote, credentials, mapped_inputs(remote, item.input), item.output,
                    job.definition["pass_threshold"], trace=(item.metadata or {}).get("trace", []), retry=True,
                )
                verdict = result["verdict"]
            except Exception:
                # Never persist transport exception text: URLs/credentials may be in it.
                verdict = {"error": "Remote scorer could not score this row"}
            return {"item_id": str(item.id), "row_id": str(item.example_id),
                    "trial_number": item.trial_number, "verdict": verdict}

        results = []
        with ThreadPoolExecutor(max_workers=remote.get("calls_at_a_time", 1)) as pool:
            futures = [pool.submit(score, item) for item in items]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                job.sent = len(results)
                job.failed += int("error" in result["verdict"])
                job.result = {"rows": results}
                job.save(update_fields=["sent", "failed", "result", "updated_at"])
        # Re-check the template's scope before committing the child.
        template.refresh_from_db()
        if template.deleted:
            raise ValueError("Template was deleted")
        with transaction.atomic():
            job.result = persist_child(job, results)
            job.status = "done"
            job.save(update_fields=["result", "status", "updated_at"])
    except Exception:
        RemoteEvalJob.objects.filter(id=job_id).update(
            status="failed", error="Remote scoring failed; completed row results remain available.", updated_at=timezone.now(),
        )
