"""Service layer for experiment operations — shared by views and ai_tools."""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ServiceError:
    message: str
    code: str = "ERROR"


#: What the older experiment create (POST /model-hub/experiments/ and
#: create_experiment below) answers: an experiment it made had no snapshot of
#: its rows and never ran. Experiments are created through the v2 create.
OLDER_CREATE_REFUSAL = (
    "This route does not create experiments. "
    "Create them with POST /model-hub/experiments/v2/."
)


def create_experiment(
    *, name, dataset_id, column_id, prompt_config, user, user_eval_template_ids=None
):
    """Refused: an experiment made this way had no snapshot of its rows and
    never ran. Experiments are created through POST /model-hub/experiments/v2/.

    Returns:
        ServiceError with OLDER_CREATE_REFUSAL; nothing is created or started.
    """
    return ServiceError(OLDER_CREATE_REFUSAL, "ROUTE_RETIRED")
