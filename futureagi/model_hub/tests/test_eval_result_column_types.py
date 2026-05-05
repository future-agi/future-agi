import pytest

from model_hub.utils.eval_result_columns import infer_eval_result_column_data_type


class _TemplateStub:
    def __init__(
        self,
        *,
        template_type="single",
        aggregation_enabled=True,
        output=None,
    ):
        self.template_type = template_type
        self.aggregation_enabled = aggregation_enabled
        self.config = {"output": output} if output is not None else {}


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        (_TemplateStub(output="reason"), "text"),
        (_TemplateStub(output="score"), "float"),
        (_TemplateStub(output="numeric"), "float"),
        (_TemplateStub(output="choices"), "array"),
        (_TemplateStub(output="Pass/Fail"), "boolean"),
        (_TemplateStub(output=None), "boolean"),
        (
            _TemplateStub(template_type="composite", aggregation_enabled=True),
            "float",
        ),
        (
            _TemplateStub(template_type="composite", aggregation_enabled=False),
            "text",
        ),
    ],
)
def test_infer_eval_result_column_data_type(template, expected):
    assert infer_eval_result_column_data_type(template) == expected
