"""Persist refused grading without inventing a score or changing model choices."""

from django.utils import timezone

from model_hub.models.choices import StatusType
from simulate.utils.eval_summary import derive_kpi_output_type


def record_evaluation_denial(call_execution, eval_config, denial):
    # FAILED is the existing terminal state used by completion/rerun selectors.
    # The explicit denial payload distinguishes blocked grading from a crash.
    if not call_execution.eval_outputs:
        call_execution.eval_outputs = {}
    call_execution.eval_outputs[str(eval_config.id)] = {
        "status": StatusType.FAILED.value,
        # Legacy readers recognize this sentinel. The code lives in denial.
        "error": "error",
        "denial": denial.as_dict(),
        "reason": str(denial),
        "name": eval_config.name,
        "timestamp": timezone.now().isoformat(),
        "output": None,
        "output_type": derive_kpi_output_type(eval_config.eval_template),
    }
    call_execution.save(update_fields=["eval_outputs"])
    eval_config.status = StatusType.FAILED.value
    eval_config.save(update_fields=["status", "updated_at"])
