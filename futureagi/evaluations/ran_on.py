"""Product names for the model an agent evaluation ran on."""

# The managed model and the Turing models, by the names the eval model
# selector shows (frontend/src/sections/evals/components/evaluatorModels.js).
# A model missing here, such as a multimodal vendor model, has no product name
# yet, so nothing names it.
_RAN_ON_LABELS = {
    "falcon_ai": "Falcon AI",
    "turing_large": "Turing Large",
    "turing_small": "Turing Small",
    "turing_flash": "Turing Flash",
}


def ran_on_label(ran_on_model: object, chosen_model: str | None) -> str | None:
    """Name the model a run ran on, to show beside the model the person chose.

    Returns None when nothing was recorded, when the run ran on the chosen
    model, or when the model has no product name. Never returns the stored id.
    """
    if not isinstance(ran_on_model, str) or ran_on_model == chosen_model:
        return None
    return _RAN_ON_LABELS.get(ran_on_model)
