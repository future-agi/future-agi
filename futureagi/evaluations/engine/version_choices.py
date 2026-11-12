"""Choice definitions shared by historical execution and result conversion."""


def version_choice_config(template, version=None):
    if version is None:
        scores = getattr(template, "choice_scores", None)
        return {
            "choice_scores": scores,
            "choices": getattr(template, "choices", None) or list(scores or {}),
            "multi_choice": getattr(template, "multi_choice", False),
        }
    snapshot = getattr(version, "config_snapshot", None) or {}
    scores = snapshot.get("choice_scores", getattr(version, "choice_scores", None))
    result = {
        "choice_scores": scores,
        "choices": snapshot.get("choices") or list(scores or {}),
        "multi_choice": snapshot.get(
            "multi_choice", getattr(version, "multi_choice", False)
        ),
    }
    if "output" in snapshot:
        result["output_type"] = snapshot["output"]
    return result
