"""Shared simulator-call identity configuration."""

from django.conf import settings

SIMULATOR_PHONE_NUMBERS = tuple(settings.SIMULATOR_PHONE_NUMBERS)

__all__ = ["SIMULATOR_PHONE_NUMBERS"]
