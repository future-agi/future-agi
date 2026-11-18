import pytest

from simulate.serializers.harness_job import phone_number_error

VALID = [
    "+14155551234",
    "+919123456789",
    "+8613812345678",
    "+5511987654321",
    "+5511912345678",
    "+4915123456789",
    "+6281234567890",
    "+447911123456",
    "+6581234567",
    "+3790123456",
    "+870123456789",
]

INVALID = [
    ("+1415555123", "exactly 10 digits after the +1 country code"),
    ("+141555512345", "exactly 10 digits after the +1 country code"),
    ("+91912345678", "exactly 10 digits after the +91 country code"),
    ("+9191234567890", "exactly 10 digits after the +91 country code"),
    ("+123", "E.164"),
    ("+0123456789", "E.164"),
    ("+1234567890123456", "E.164"),
    ("4155551234", "E.164"),
    ("", "E.164"),
]


@pytest.mark.parametrize("number", VALID)
def test_valid_numbers_are_accepted(number):
    assert phone_number_error(number) is None


@pytest.mark.parametrize("number,reason", INVALID)
def test_invalid_numbers_name_the_reason(number, reason):
    assert reason in phone_number_error(number)
