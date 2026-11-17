"""Text fields shared by experiment creation and metadata-only updates."""

from rest_framework import serializers


class ExperimentNameField(serializers.CharField):
    default_error_messages = {
        "blank": "Enter an experiment name.",
        "null": "Enter an experiment name.",
        "required": "Enter an experiment name.",
        "invalid": "Enter an experiment name as text.",
        "max_length": "Use 255 characters or fewer for the experiment name.",
    }

    def __init__(self, **kwargs):
        super().__init__(max_length=255, trim_whitespace=True, **kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class ExperimentDescriptionField(serializers.CharField):
    default_error_messages = {
        "null": "Enter a description as text, or leave it empty.",
        "invalid": "Enter a description as text, or leave it empty.",
        "max_length": "Use 4000 characters or fewer for the description.",
    }

    def __init__(self, **kwargs):
        super().__init__(
            max_length=4000, allow_blank=True, trim_whitespace=False, **kwargs
        )

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)
