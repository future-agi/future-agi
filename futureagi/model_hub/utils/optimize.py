def get_right_answer_columns(metrics=None):
    if metrics is None:
        metrics = []
    columns = [
        {"label": "Input", "value": "input", "enabled": True},
        {"label": "Output", "value": "output", "enabled": True},
    ]
    for metric in metrics:
        columns.append(
            {
                "label": f"(Old) {metric.name}",
                "value": f"{str(metric.id)}-old",
                "enabled": True,
            }
        )
        columns.append(
            {
                "label": f"(New) {metric.name}",
                "value": f"{str(metric.id)}-new",
                "enabled": True,
            }
        )

    columns.append({"label": "Right answer", "value": "rightAnswer", "enabled": True})

    return columns


def get_prompt_template_columns(metrics=None, k_prompts=None):
    if k_prompts is None:
        k_prompts = []
    if metrics is None:
        metrics = []
    columns = [
        {"label": "Input", "value": "input", "enabled": True},
        {"label": "Output", "value": "output", "enabled": True},
    ]
    for metric in metrics:
        for idx in range(len(k_prompts)):
            columns.append(
                {
                    "label": f"(T{idx + 1}) {metric.name}",
                    "value": f"{str(metric.id)}-{idx}",
                    "enabled": True,
                }
            )
        columns.append(
            {
                "label": f"(Original) {metric.name}",
                "value": f"{str(metric.id)}-original",
                "enabled": True,
            }
        )

    return columns
