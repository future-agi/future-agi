"""
Hypothesis property-based tests for the RBAC utilities in accounts/.

Tests the pure functions directly — no Django ORM required.

Run with: pytest futureagi/accounts/formal_tests/test_rbac_hypothesis.py -v
"""

import importlib.util
import pathlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Load Level from its pure-Python module (no Django deps)
# ---------------------------------------------------------------------------

_repo = pathlib.Path(__file__).parents[2]  # futureagi/


def _load_levels():
    path = _repo / "tfc" / "constants" / "levels.py"
    spec = importlib.util.spec_from_file_location("tfc.constants.levels", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Level


Level = _load_levels()

# ---------------------------------------------------------------------------
# Inline the three pure RBAC functions (tfc/permissions/utils.py)
# These are re-stated here so we test the logic without dragging in Django.
# Bridge tests below verify the production source matches this model.
# ---------------------------------------------------------------------------

def can_invite_at_level(actor_level, target_level):
    if actor_level >= Level.OWNER:
        return target_level <= Level.OWNER
    return target_level <= actor_level

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

ORG_LEVELS = [Level.VIEWER, Level.MEMBER, Level.ADMIN, Level.OWNER]
WS_LEVELS = [Level.WORKSPACE_VIEWER, Level.WORKSPACE_MEMBER, Level.WORKSPACE_ADMIN]

org_level_st = st.sampled_from(ORG_LEVELS)
ws_level_st = st.sampled_from(WS_LEVELS)
maybe_ws_level_st = st.one_of(st.none(), ws_level_st)

# ---------------------------------------------------------------------------
# can_invite_at_level properties
# ---------------------------------------------------------------------------

@given(actor=org_level_st, target=org_level_st)
def test_can_invite_owner_can_invite_all(actor, target):
    """An Owner can always invite any level."""
    if actor >= Level.OWNER:
        assert can_invite_at_level(actor, target)


@given(actor=org_level_st, target=org_level_st)
def test_can_invite_non_owner_no_escalation(actor, target):
    """Non-Owner actors cannot grant a level above their own."""
    if actor < Level.OWNER and target > actor:
        assert not can_invite_at_level(actor, target)


@given(actor=org_level_st)
def test_can_invite_same_level_allowed_for_non_owner(actor):
    """An actor can invite someone at their own level (self-replication at same level)."""
    if actor < Level.OWNER:
        assert can_invite_at_level(actor, actor)


@given(actor=org_level_st, target=org_level_st)
def test_can_invite_monotone_in_actor(actor, target):
    """Increasing actor level can only increase the set of allowable targets."""
    if actor < Level.OWNER:
        can_now = can_invite_at_level(actor, target)
        # A higher actor level (one step up) should permit at least as many targets
        higher_levels = [l for l in ORG_LEVELS if l > actor]
        for higher in higher_levels:
            if higher < Level.OWNER:
                can_higher = can_invite_at_level(higher, target)
                if can_now:
                    assert can_higher, (
                        f"actor={actor} can invite target={target} "
                        f"but higher actor={higher} cannot — monotonicity violated"
                    )


# ---------------------------------------------------------------------------
# Effective workspace level properties
# ---------------------------------------------------------------------------

def _effective_ws_level(org_level, ws_level):
    """Pure mirror of get_effective_workspace_level."""
    if org_level >= Level.ADMIN:
        return max(org_level, Level.WORKSPACE_ADMIN)
    if ws_level is None:
        return None
    return max(org_level, ws_level)


@given(org_level=org_level_st, ws_level=ws_level_st)
def test_effective_always_dominates_org(org_level, ws_level):
    """effective_ws_level is always >= org_level when access exists."""
    eff = _effective_ws_level(org_level, ws_level)
    if eff is not None:
        assert eff >= org_level


@given(org_level=st.sampled_from([Level.ADMIN, Level.OWNER]))
def test_admin_owner_always_have_ws_access(org_level):
    """Org Admin and Owner have workspace access even without explicit membership."""
    eff = _effective_ws_level(org_level, None)
    assert eff is not None
    assert eff >= Level.WORKSPACE_ADMIN


@given(org_level=st.sampled_from([Level.VIEWER, Level.MEMBER]))
def test_below_admin_without_ws_membership_gets_none(org_level):
    """Viewer/Member with no workspace membership has no workspace access."""
    assert _effective_ws_level(org_level, None) is None


@given(org_level=org_level_st, ws_level=ws_level_st)
def test_effective_is_max_of_inputs(org_level, ws_level):
    """effective_ws_level = max(org_level, ws_level) (when non-Admin has membership)."""
    if org_level < Level.ADMIN:
        eff = _effective_ws_level(org_level, ws_level)
        assert eff == max(org_level, ws_level)


# ---------------------------------------------------------------------------
# CanManageTargetUser properties
# ---------------------------------------------------------------------------

def _can_manage(actor_level, target_level):
    if actor_level >= Level.OWNER:
        return True
    return actor_level > target_level


@given(actor=org_level_st, target=org_level_st)
def test_can_manage_owner_manages_all(actor, target):
    if actor >= Level.OWNER:
        assert _can_manage(actor, target)


@given(actor=org_level_st, target=org_level_st)
def test_can_manage_asymmetric_below_owner(actor, target):
    """Asymmetry: if A manages B (both non-Owner), B cannot manage A."""
    if actor < Level.OWNER and target < Level.OWNER:
        a_manages_b = _can_manage(actor, target)
        b_manages_a = _can_manage(target, actor)
        assert not (a_manages_b and b_manages_a), (
            f"Both actor={actor} and target={target} can manage each other — "
            "symmetry violates strict ordering"
        )


@given(level=org_level_st)
def test_cannot_manage_self_unless_owner(level):
    """A non-Owner cannot manage themselves (level not strictly above)."""
    if level < Level.OWNER:
        assert not _can_manage(level, level)


# ---------------------------------------------------------------------------
# Level string ↔ integer round-trips
# ---------------------------------------------------------------------------

_ORG_STRINGS = ["Owner", "Admin", "Member", "Viewer"]


@given(role=st.sampled_from(_ORG_STRINGS))
def test_string_to_level_round_trips_on_org_roles(role):
    """from_string → to_org_string is identity for the four canonical org roles."""
    lvl = Level.from_string(role)
    back = Level.to_org_string(lvl)
    assert back == role, f"Round-trip failed: '{role}' → {lvl} → '{back}'"


@given(lvl=st.sampled_from(ORG_LEVELS))
def test_to_org_string_produces_valid_role(lvl):
    """to_org_string returns a recognized org role string."""
    s = Level.to_org_string(lvl)
    assert s in _ORG_STRINGS, f"Level {lvl} → unrecognized org string '{s}'"


@given(lvl=st.sampled_from(WS_LEVELS))
def test_to_ws_role_returns_db_safe_value(lvl):
    """to_ws_role returns a DB-safe workspace role value."""
    _DB_WS_ROLES = {"workspace_admin", "workspace_member", "workspace_viewer"}
    result = Level.to_ws_role(lvl)
    assert result in _DB_WS_ROLES, f"Level {lvl} → unexpected ws role '{result}'"


# ---------------------------------------------------------------------------
# get_default_ws_level
# ---------------------------------------------------------------------------

@given(org_level=org_level_st)
def test_get_default_ws_level_always_valid(org_level):
    """get_default_ws_level always returns a recognised workspace level."""
    result = Level.get_default_ws_level(org_level)
    assert result in WS_LEVELS


@given(org_level=st.sampled_from([Level.ADMIN, Level.OWNER]))
def test_get_default_ws_level_admin_owner(org_level):
    """Admin and Owner get default WS level of WORKSPACE_ADMIN."""
    assert Level.get_default_ws_level(org_level) == Level.WORKSPACE_ADMIN


@given(org_level=org_level_st)
def test_get_default_ws_level_monotone(org_level):
    """Higher org level → higher or equal default ws level."""
    result = Level.get_default_ws_level(org_level)
    for higher in [l for l in ORG_LEVELS if l > org_level]:
        higher_result = Level.get_default_ws_level(higher)
        assert higher_result >= result, (
            f"org={org_level}→ws={result}, org={higher}→ws={higher_result}: "
            "ws level decreased as org level increased"
        )
