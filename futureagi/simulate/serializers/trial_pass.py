"""A run's trials as its header reads them, served on every surface that shows the run's pass."""

from rest_framework import serializers


class TrialPassFieldsSerializer(serializers.Serializer):
    trials_passed = serializers.IntegerField(help_text="Comparable trials that passed.")
    trials_evaluated = serializers.IntegerField(
        help_text="Comparable trials that passed or failed: the trial pass rate's base."
    )
    trials_inconclusive = serializers.IntegerField(
        help_text="Comparable trials that errored or were inconclusive, counted apart from the base."
    )
    trials_excluded_not_comparable = serializers.IntegerField(
        help_text="Trials left out because they did not start from the seeded records."
    )
    trial_pass_rate = serializers.FloatField(
        allow_null=True,
        help_text="Trials passed over trials evaluated, as a percent; null when none was evaluated.",
    )
