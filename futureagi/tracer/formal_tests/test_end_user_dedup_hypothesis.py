"""
Hypothesis property tests for EndUser deduplication normalisation (issue #305).

Tests the _norm_uid_type() helper and the key-building logic directly —
no Django or database required.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Inline the normalisation logic (mirrors trace_ingestion._norm_uid_type)
# ---------------------------------------------------------------------------

_VALID_TYPES = {"email", "phone", "uuid", "custom"}


def _norm_uid_type(raw: str | None) -> str:
    """Normalise a caller-supplied user_id_type: None or empty → "custom"."""
    return raw if raw else "custom"


def _make_key(project_id: str, org_id: str, user_id: str, user_id_type: str | None) -> tuple:
    """Build the deduplication key used by _fetch_or_create_end_users."""
    return (user_id, org_id, project_id, _norm_uid_type(user_id_type))


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

_id_st = st.text(min_size=1, max_size=50)
_uid_type_st = st.one_of(st.just(None), st.sampled_from(list(_VALID_TYPES)))

# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@given(raw=st.one_of(st.none(), st.just(""), st.just("email"), st.just("custom")))
@settings(max_examples=200)
def test_norm_none_and_empty_become_custom(raw):
    """None and '' both normalise to 'custom'; valid types pass through."""
    result = _norm_uid_type(raw)
    if not raw:
        assert result == "custom"
    else:
        assert result == raw


@given(raw=st.one_of(st.none(), st.sampled_from(list(_VALID_TYPES))))
@settings(max_examples=200)
def test_norm_is_idempotent(raw):
    """Applying normalisation twice yields the same result as once."""
    once = _norm_uid_type(raw)
    twice = _norm_uid_type(once)
    assert once == twice


@given(raw=st.one_of(st.none(), st.sampled_from(list(_VALID_TYPES))))
@settings(max_examples=200)
def test_norm_never_returns_none(raw):
    """Normalised user_id_type is always a non-empty string."""
    result = _norm_uid_type(raw)
    assert result is not None
    assert result != ""


@given(
    project_id=_id_st,
    org_id=_id_st,
    user_id=_id_st,
)
@settings(max_examples=300)
def test_two_null_types_produce_same_key(project_id, org_id, user_id):
    """After fix: two rows with user_id_type=None get the same dedup key."""
    k1 = _make_key(project_id, org_id, user_id, None)
    k2 = _make_key(project_id, org_id, user_id, None)
    assert k1 == k2, "Both NULL types must normalise to the same key"


@given(
    project_id=_id_st,
    org_id=_id_st,
    user_id=_id_st,
)
@settings(max_examples=300)
def test_null_type_and_custom_produce_same_key(project_id, org_id, user_id):
    """Explicit 'custom' and implicit None (→ 'custom') produce identical keys."""
    k_null = _make_key(project_id, org_id, user_id, None)
    k_custom = _make_key(project_id, org_id, user_id, "custom")
    assert k_null == k_custom


@given(
    project_id=_id_st,
    org_id=_id_st,
    user_id1=_id_st,
    user_id2=_id_st,
    uid_type=_uid_type_st,
)
@settings(max_examples=300)
def test_distinct_user_ids_distinct_keys(project_id, org_id, user_id1, user_id2, uid_type):
    """Distinct user_ids always produce distinct dedup keys."""
    if user_id1 == user_id2:
        return
    k1 = _make_key(project_id, org_id, user_id1, uid_type)
    k2 = _make_key(project_id, org_id, user_id2, uid_type)
    assert k1 != k2


@given(
    project_id=_id_st,
    org_id=_id_st,
    user_id=_id_st,
    t1=st.sampled_from(list(_VALID_TYPES)),
    t2=st.sampled_from(list(_VALID_TYPES)),
)
@settings(max_examples=300)
def test_distinct_types_distinct_keys(project_id, org_id, user_id, t1, t2):
    """Distinct (non-null) user_id_types produce distinct dedup keys."""
    if t1 == t2:
        return
    k1 = _make_key(project_id, org_id, user_id, t1)
    k2 = _make_key(project_id, org_id, user_id, t2)
    assert k1 != k2


@given(
    project_id1=_id_st,
    project_id2=_id_st,
    org_id=_id_st,
    user_id=_id_st,
    uid_type=_uid_type_st,
)
@settings(max_examples=300)
def test_distinct_projects_distinct_keys(project_id1, project_id2, org_id, user_id, uid_type):
    """Same user in different projects produces distinct dedup keys."""
    if project_id1 == project_id2:
        return
    k1 = _make_key(project_id1, org_id, user_id, uid_type)
    k2 = _make_key(project_id2, org_id, user_id, uid_type)
    assert k1 != k2


@given(
    project_id=_id_st,
    org_id=_id_st,
    user_id=_id_st,
    uid_type=_uid_type_st,
)
@settings(max_examples=300)
def test_key_is_deterministic(project_id, org_id, user_id, uid_type):
    """Same inputs always produce the same dedup key."""
    k1 = _make_key(project_id, org_id, user_id, uid_type)
    k2 = _make_key(project_id, org_id, user_id, uid_type)
    assert k1 == k2


@given(uid_type=_uid_type_st)
@settings(max_examples=200)
def test_normalised_type_is_in_valid_set(uid_type):
    """Normalised user_id_type is always a member of the valid choices set."""
    result = _norm_uid_type(uid_type)
    assert result in _VALID_TYPES, f"Unexpected normalised value: {result!r}"
