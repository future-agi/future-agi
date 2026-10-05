"""PostgreSQL expressions shared by simulation result read models."""

from django.contrib.postgres.fields import ArrayField
from django.db.models import (
    Case,
    Expression,
    F,
    FloatField,
    Func,
    QuerySet,
    TextField,
    Value,
    When,
)
from django.db.models.aggregates import Aggregate
from django.db.models.expressions import Col
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Greatest, Least, NullIf
from django.db.models.lookups import GreaterThan, Regex
from django.db.models.sql.datastructures import BaseTable

NUMERIC_JSON_PATTERN = r"^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"


class _LateralProjection(BaseTable):
    """Keep one scalar annotation from being expanded at every ORM reference."""

    def __init__(self, alias: str | None, parent_alias: str, expression: Expression):
        super().__init__("simulation_call_verdict", alias)
        self.parent_alias = parent_alias
        self.expression = expression

    def as_sql(self, compiler, connection):
        sql, params = compiler.compile(self.expression)
        alias = connection.ops.quote_name(self.table_alias)
        # OFFSET 0 prevents PostgreSQL from inlining the expensive expression.
        return f"CROSS JOIN LATERAL (SELECT {sql} AS value OFFSET 0) {alias}", params

    def relabeled_clone(self, change_map):
        return self.__class__(
            change_map.get(self.table_alias, self.table_alias),
            change_map.get(self.parent_alias, self.parent_alias),
            self.expression.relabeled_clone(change_map),
        )


def project_annotation(queryset: QuerySet, name: str) -> QuerySet:
    """Project an existing annotation once per call, retaining an ORM queryset."""
    queryset = queryset.all()
    query = queryset.query
    expression = query.annotations[name]
    alias = query.join(_LateralProjection(None, query.get_initial_alias(), expression))
    field = expression.output_field.clone()
    field.set_attributes_from_name("value")
    field.model = None
    query.annotations[name] = Col(alias, field)
    return queryset


class NormalizedEvalNumber(Func):
    """Keep numeric conversion compact during ORM expression traversal."""

    output_field = FloatField()

    def __init__(self, expression, *, pass_fail: bool):
        super().__init__(expression)
        self.pass_fail = pass_fail

    def as_sql(self, compiler, connection, **extra_context):
        normalized = self.source_expressions[0]
        numeric = Cast(normalized, FloatField())
        if self.pass_fail:
            converted = Case(
                When(GreaterThan(numeric, Value(0.0)), then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        else:
            converted = Least(
                Greatest(
                    Case(
                        When(
                            GreaterThan(numeric, Value(1.0)),
                            then=numeric / Value(100.0),
                        ),
                        default=numeric,
                        output_field=FloatField(),
                    ),
                    Value(0.0),
                ),
                Value(1.0),
            )
        expression = Case(
            When(
                Regex(
                    normalized,
                    Value(
                        r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
                    ),
                ),
                then=converted,
            ),
            output_field=FloatField(),
        )
        return compiler.compile(expression)


class MatchingListGroups(Func):
    """Emit each requested JSON-list membership once, including the empty group."""

    output_field = TextField()
    set_returning = True

    def __init__(self, expression, keys: list[str], empty_label: str):
        super().__init__(
            expression,
            Value(keys, output_field=ArrayField(TextField())),
            Value(empty_label),
        )

    def as_sql(self, compiler, connection, **extra_context):
        value, keys, empty = [
            compiler.compile(expression) for expression in self.source_expressions
        ]
        sql = (
            "unnest(ARRAY(SELECT candidate.key "
            f"FROM (VALUES ({value[0]})) AS membership(value) "
            f"CROSS JOIN unnest({keys[0]}) AS candidate(key) "
            f"WHERE CASE WHEN candidate.key = {empty[0]} "
            "THEN membership.value = '[]'::jsonb "
            "ELSE membership.value @> jsonb_build_array(candidate.key) END))"
        )
        return sql, [*value[1], *keys[1], *empty[1]]


class PercentileCont(Aggregate):
    function = "PERCENTILE_CONT"
    name = "PercentileCont"
    output_field = FloatField()
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"

    def __init__(self, expression, percentile: float, **extra):
        super().__init__(expression, percentile=percentile, **extra)


def _json_text(field: str | Expression, *keys: str):
    expression = F(field) if isinstance(field, str) else field
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
