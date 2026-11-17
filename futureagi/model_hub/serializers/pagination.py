"""Serializer pagination bounds with the shared, actionable page-cap error code."""

from rest_framework import serializers

from tfc.utils.pagination import MAX_PAGE_SIZE, PageSizeTooLarge


class PageSizeField(serializers.IntegerField):
    """Keep field validation envelopes while matching the paginator's cap error.

    Callers also declare max_value so generated contracts expose the ceiling.
    Use a ValidationError rather than raising an APIException out of is_valid:
    existing manual serializer consumers must retain their normal 400 path.
    """

    def run_validators(self, value):
        if value > MAX_PAGE_SIZE:
            raise serializers.ValidationError(
                PageSizeTooLarge.default_detail,
                code=PageSizeTooLarge.default_code,
            )
        return super().run_validators(value)
