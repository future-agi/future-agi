"""Additive coverage of observed usage; never a capture-completeness claim."""

import math
from typing import Literal, TypedDict

from rest_framework import serializers


class CostCoverage(TypedDict):
    status: Literal["complete", "partial", "unknown"]
    known_subtotal: float
    known_fact_count: int
    unknown_fact_count: int


class CostCoverageSerializer(serializers.Serializer):
    """USD coverage over the same latest-live additive facts as the metric.

    ``known_subtotal`` is always a sum, even beside an average-cost metric.
    Complete means no observed monetary facts are unpriced, not that all
    requests were captured. Omit the entire object on an unclassified legacy
    response; never default absent coverage to complete.
    """

    status = serializers.ChoiceField(choices=("complete", "partial", "unknown"))
    known_subtotal = serializers.FloatField(
        min_value=0,
        help_text="Sum in USD of known observed contributions, never an average.",
    )
    known_fact_count = serializers.IntegerField(
        min_value=0,
        help_text="Observed additive monetary facts with known cost, including zero.",
    )
    unknown_fact_count = serializers.IntegerField(
        min_value=0,
        help_text="Observed additive monetary facts with unknown cost, not missing requests.",
    )

    def validate_known_subtotal(self, value: float) -> float:
        if not math.isfinite(value):
            raise serializers.ValidationError("Known subtotal must be finite.")
        return value

    def validate(self, attrs: CostCoverage) -> CostCoverage:
        expected = (
            "complete"
            if attrs["unknown_fact_count"] == 0
            else "partial"
            if attrs["known_fact_count"] > 0
            else "unknown"
        )
        if attrs["status"] != expected:
            raise serializers.ValidationError(
                {"status": "Coverage status must agree with the observed fact counts."}
            )
        if attrs["known_fact_count"] == 0 and attrs["known_subtotal"] != 0:
            raise serializers.ValidationError(
                {"known_subtotal": "A subtotal without known facts must be zero."}
            )
        return attrs


class CostCoverageBucketSerializer(CostCoverageSerializer):
    """Companion to an existing cost-series point with the identical timestamp."""

    timestamp = serializers.CharField()


class CaptureCoverageSerializer(serializers.Serializer):
    """Observed request coverage, separate from pricing and SQL completeness."""

    scope = serializers.ChoiceField(choices=("observed_requests",))
    source_summary_present = serializers.BooleanField()
    request_count = serializers.IntegerField(
        min_value=0,
        help_text="Observed request spans only; never inferred from source summary totals.",
    )
    coverage_unknown = serializers.BooleanField(
        help_text=(
            "Whether capture completeness is unproved. Matching summary totals "
            "or a successful exact query do not prove capture completeness."
        ),
    )
