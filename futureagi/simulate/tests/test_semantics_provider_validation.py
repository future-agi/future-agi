import pytest
from django.core.exceptions import ValidationError

from simulate.semantics import validate_provider_sent_objects


class TestValidateProviderSentObjects:
    def test_twilio_key_is_accepted(self):
        # should not raise
        validate_provider_sent_objects({"twilio": {"call_sid": "CA123"}})

    def test_vapi_key_is_accepted(self):
        # confirms payload shape itself was never the issue (#2662 repro step)
        validate_provider_sent_objects({"vapi": {"id": "abc"}})

    def test_unknown_key_is_rejected(self):
        with pytest.raises(ValidationError):
            validate_provider_sent_objects({"not_a_real_provider": {}})