"""PostgreSQL expressions shared by simulation result read models."""

from django.db.models import Case, F, FloatField, TextField, Value, When
from django.db.models.aggregates import Aggregate
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, NullIf

NUMERIC_JSON_PATTERN = r"^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"


class PercentileCont(Aggregate):
    function = "PERCENTILE_CONT"
    name = "PercentileCont"
    output_field = FloatField()
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"

    def __init__(self, expression, percentile: float, **extra):
        super().__init__(expression, percentile=percentile, **extra)


def _json_text(field: str, *keys: str):
    expression = F(field)
    for key in keys:
        expression = KeyTextTransform(key, expression)
    return NullIf(expression, Value(""), output_field=TextField())


def _safe_json_float(field: str, *keys: str):
    lookup = "__".join((field, *keys, "regex"))
    return Case(
        When(
            **{
                lookup: NUMERIC_JSON_PATTERN,
                "then": Cast(_json_text(field, *keys), FloatField()),
            }
        ),
        default=None,
        output_field=FloatField(),
    )
