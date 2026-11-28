"""Seed the state left by a selector failure, without racing a running workflow."""

import json
import os
from datetime import timedelta


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
    import django

    django.setup()
    from django.db import transaction
    from django.utils import timezone

    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
    from tracer.models.project import Project
    from tracer.services.eval_tasks.selection_failure import SelectionFailure

    project = Project.objects.get(
        pk=os.environ["SEED_PROJECT_ID"],
        organization_id=os.environ["SEED_ORG_ID"],
        workspace_id=os.environ["SEED_WORKSPACE_ID"],
    )
    name = os.environ["SEED_TASK_NAME"]
    now = timezone.now()
    with transaction.atomic():
        # The cross-project task list only shows tasks with a visible eval config.
        config = CustomEvalConfig.objects.create(
            project=project,
            name=f"{name}-eval",
            eval_template=EvalTemplate.objects.get(owner="system", name="contains_valid_link"),
            mapping={"text": "output.value"},
        )
        task = EvalTask.objects.create(
            project=project,
            name=name,
            run_type=RunType.HISTORICAL,
            filters={
                "project_id": str(project.pk),
                "date_range": [(now - timedelta(hours=1)).isoformat(), now.isoformat()],
            },
            last_run=now,
            status=EvalTaskStatus.FAILED,
            failure_reason=SelectionFailure.READ_BUDGET.name,
        )
        task.evals.add(config)
    print(json.dumps({"taskId": str(task.pk)}))


if __name__ == "__main__":
    main()
