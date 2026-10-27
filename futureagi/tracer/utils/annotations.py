"""Annotation subquery helpers.

Provides ``build_annotation_subqueries`` which annotates a ``Trace`` queryset
with aggregated annotation data (scores, counts, annotator details) for each
annotation label.

Reads from the unified ``Score`` model rather than ``TraceAnnotation``.
"""

from django.db.models import (
    Avg,
    Case,
    Count,
    F,
    FloatField,
    IntegerField,
    JSONField,
    OuterRef,
    Q,
    Subquery,
    TextField,
    Value,
    When,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, Floor, JSONObject, NullIf, Round

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.score import Score
from tracer.utils.aggregates import JSONBObjectAgg


def build_annotation_subqueries(
    base_query, annotation_labels, organization, span_filter_kwargs=None, source_q=None
):
    """
    Annotate *base_query* with aggregated annotation subqueries for every
    label in *annotation_labels*.

    For NUMERIC/STAR labels the annotation value is the floored average of
    all annotator scores.  For THUMBS_UP_DOWN the value contains
    thumbs_up/thumbs_down counts.  For CATEGORICAL it contains per-choice
    counts.  All types include an ``annotators`` map keyed by user id.

    Args:
        base_query: The queryset to annotate.
        annotation_labels: AnnotationsLabels queryset.
        organization: The organization used for filtering annotations.
        span_filter_kwargs: Optional dict to override the default outer-ref
            filter.  Defaults to trace-level filtering which matches scores
            created directly on traces OR on observation spans belonging to
            the trace.  For span-level queries pass
            ``{"observation_span_id": OuterRef("id")}``.
        source_q: Optional prebuilt Q matching scores to the outer row.
            Takes precedence over ``span_filter_kwargs`` (allows OR scopes).

    Returns:
        The annotated *base_query*.
    """
    annotator_name = Coalesce(
        NullIf("annotator__name", Value("")),
        NullIf("annotator__email", Value("")),
        Value("Unknown"),
    )

    for label in annotation_labels:
        if source_q is not None:
            base_ann_q = (
                Q(
                    label_id=label.id,
                    organization=organization,
                    deleted=False,
                )
                & source_q
            )
        elif span_filter_kwargs is not None:
            base_ann_q = Q(
                **span_filter_kwargs,
                label_id=label.id,
                deleted=False,
            )
        else:
            # Match scores on the trace directly OR on any observation span
            # belonging to the trace.  This covers both source_type="trace"
            # and source_type="observation_span" annotations.
            base_ann_q = Q(
                label_id=label.id,
                organization=organization,
                deleted=False,
            ) & (
                Q(trace_id=OuterRef("id"))
                | Q(observation_span__trace_id=OuterRef("id"))
            )

        if label.type in [
            AnnotationTypeChoices.NUMERIC.value,
            AnnotationTypeChoices.STAR.value,
        ]:
            # NUMERIC stores {"value": float}, STAR stores {"rating": float}
            value_key = (
                "value"
                if label.type == AnnotationTypeChoices.NUMERIC.value
                else "rating"
            )
            score_field = Cast(KeyTextTransform(value_key, "value"), FloatField())

            # STAR is integer (Floor); NUMERIC can be sub-integer (Round to keep precision).
            avg_score = (
                Floor(Avg(score_field))
                if label.type == AnnotationTypeChoices.STAR.value
                else Round(Avg(score_field), 2)
            )
            subq = (
                Score.objects.filter(base_ann_q)
                .exclude(**{f"value__{value_key}__isnull": True})
                .values("label_id")
                .annotate(
                    result=JSONObject(
                        score=avg_score,
                        annotators=JSONBObjectAgg(
                            Cast(F("annotator_id"), TextField()),
                            JSONObject(
                                user_id=F("annotator_id"),
                                user_name=annotator_name,
                                score=score_field,
                            ),
                            # jsonb_object_agg() requires NOT NULL keys.
                            filter=Q(annotator_id__isnull=False),
                        ),
                    )
                )
                .values("result")[:1]
            )
            base_query = base_query.annotate(
                **{f"annotation_{label.id}": Subquery(subq, output_field=JSONField())}
            )

        elif label.type == AnnotationTypeChoices.THUMBS_UP_DOWN.value:
            subq = (
                Score.objects.filter(base_ann_q)
                .exclude(value__value__isnull=True)
                .values("label_id")
                .annotate(
                    result=JSONObject(
                        thumbs_up=Count(
                            "id",
                            filter=Q(value__value="up"),
                        ),
                        thumbs_down=Count(
                            "id",
                            filter=Q(value__value="down"),
                        ),
                        annotators=JSONBObjectAgg(
                            Cast(F("annotator_id"), TextField()),
                            JSONObject(
                                user_id=F("annotator_id"),
                                user_name=annotator_name,
                                score=Case(
                                    When(
                                        value__value="up",
                                        then=Value(100.0),
                                    ),
                                    When(
                                        value__value="down",
                                        then=Value(0.0),
                                    ),
                                    default=None,
                                    output_field=FloatField(),
                                ),
                            ),
                            filter=Q(annotator_id__isnull=False),
                        ),
                    )
                )
                .values("result")[:1]
            )
            base_query = base_query.annotate(
                **{f"annotation_{label.id}": Subquery(subq, output_field=JSONField())}
            )

        elif label.type == AnnotationTypeChoices.CATEGORICAL.value:
            parsed_choices = [
                option["label"] for option in label.settings.get("options", [])
            ]
            if parsed_choices:
                subq = (
                    Score.objects.filter(base_ann_q)
                    .exclude(value__selected__isnull=True)
                    .values("label_id")
                    .annotate(
                        result=JSONObject(
                            **{
                                f"{choice}": Count(
                                    Case(
                                        When(
                                            value__selected__contains=[choice],
                                            then=1,
                                        ),
                                        default=None,
                                        output_field=IntegerField(),
                                    )
                                )
                                for choice in parsed_choices
                            },
                            annotators=JSONBObjectAgg(
                                Cast(F("annotator_id"), TextField()),
                                JSONObject(
                                    user_id=F("annotator_id"),
                                    user_name=annotator_name,
                                    value=F("value__selected"),
                                ),
                                filter=Q(value__selected__isnull=False),
                            ),
                        )
                    )
                    .values("result")[:1]
                )
                base_query = base_query.annotate(
                    **{
                        f"annotation_{label.id}": Subquery(
                            subq, output_field=JSONField()
                        )
                    }
                )
            else:
                base_query = base_query.annotate(
                    **{f"annotation_{label.id}": Value(None, output_field=JSONField())}
                )

        elif label.type == AnnotationTypeChoices.TEXT.value:
            subq = (
                Score.objects.filter(base_ann_q)
                .exclude(value__text__isnull=True)
                .exclude(value__text="")
                .values("label_id")
                .annotate(
                    result=JSONObject(
                        annotators=JSONBObjectAgg(
                            Cast(F("annotator_id"), TextField()),
                            JSONObject(
                                user_id=F("annotator_id"),
                                user_name=annotator_name,
                                value=F("value__text"),
                            ),
                            filter=Q(annotator_id__isnull=False),
                        ),
                    )
                )
                .values("result")[:1]
            )
            base_query = base_query.annotate(
                **{f"annotation_{label.id}": Subquery(subq, output_field=JSONField())}
            )

    return base_query
