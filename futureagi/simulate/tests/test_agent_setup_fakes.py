import pytest

from simulate.services.agent_setup import AgentStoreError, AgentSummary
from simulate.tests.fakes import make_agent_summary


def test_default_is_a_complete_stored_hosted_agent():
    summary = make_agent_summary()

    assert isinstance(summary, AgentSummary)
    assert summary.agent_stored is True
    assert summary.kind == "hosted"
    assert summary.unit_ids == ("main",)
    assert summary.credential_id is not None
    assert summary.key_present is True
    assert summary.provider_info_complete is True
    assert summary.missing == ()


def test_missing_words_make_the_provider_info_incomplete_unless_given():
    incomplete = make_agent_summary(
        missing=("credential",), credential_id=None, key_present=False
    )
    explicit = make_agent_summary(missing=("credential",), provider_info_complete=True)

    assert incomplete.provider_info_complete is False
    assert explicit.provider_info_complete is True


def test_plain_column_key_and_legacy_agent_can_be_described():
    plain = make_agent_summary(credential_id=None, key_present=True)
    legacy = make_agent_summary(agent_stored=False, kind=None, setup_hash=None)

    assert plain.credential_id is None and plain.key_present is True
    assert legacy.kind is None and legacy.setup_hash is None


def test_unknown_field_is_refused():
    with pytest.raises(TypeError):
        make_agent_summary(provider_type="vapi")


def test_store_error_text_is_its_code():
    error = AgentStoreError("agent_schema_unavailable")

    assert str(error) == error.code == "agent_schema_unavailable"
