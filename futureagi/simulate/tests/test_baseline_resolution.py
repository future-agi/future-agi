import uuid

import pytest

from simulate.utils.baseline import resolve_baseline_id


@pytest.mark.unit
class TestResolveBaselineId:
    def test_returns_none_for_non_dict_metadata(self):
        assert resolve_baseline_id(None, is_replay=True) is None
        assert resolve_baseline_id("session-1", is_replay=True) is None
        assert resolve_baseline_id([], is_replay=True) is None

    def test_returns_none_for_empty_metadata(self):
        assert resolve_baseline_id({}, is_replay=True) is None

    def test_falls_back_to_the_intent_id_for_replay_rows(self):
        intent_id = str(uuid.uuid4())

        assert resolve_baseline_id({"intent_id": intent_id}, is_replay=True) == intent_id

    def test_never_falls_back_to_the_intent_id_outside_replay(self):
        resolved = resolve_baseline_id(
            {"intent_id": str(uuid.uuid4())}, is_replay=False
        )

        assert resolved is None

    @pytest.mark.parametrize(
        "synthetic_id", ["UC-01", "UC-42", "use-case-3", "", "not-a-uuid"]
    )
    def test_rejects_synthetic_intent_ids(self, synthetic_id):
        assert resolve_baseline_id({"intent_id": synthetic_id}, is_replay=True) is None

    def test_rejects_non_uuid_session_and_trace_ids(self):
        metadata = {"session_id": "UC-07", "trace_id": "UC-08"}

        assert resolve_baseline_id(metadata, is_replay=True) is None

    def test_rejects_a_null_session_id(self):
        """Most legacy rows carry the key with a null value, not a usable id."""
        metadata = {"session_id": None, "intent_id": str(uuid.uuid4())}

        assert resolve_baseline_id(metadata, is_replay=True) == metadata["intent_id"]

    def test_rejects_non_string_ids(self):
        assert (
            resolve_baseline_id({"session_id": uuid.uuid4()}, is_replay=True) is None
        )

    def test_an_explicit_id_outranks_the_intent_id_fallback(self):
        session_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        intent_id = str(uuid.uuid4())

        assert (
            resolve_baseline_id(
                {"session_id": session_id, "trace_id": trace_id}, is_replay=True
            )
            == session_id
        )
        assert resolve_baseline_id({"trace_id": trace_id}, is_replay=True) == trace_id
        assert (
            resolve_baseline_id(
                {"trace_id": trace_id, "intent_id": intent_id}, is_replay=True
            )
            == trace_id
        )
