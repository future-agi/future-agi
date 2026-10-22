"""The enum -> registry contract.

``registry._build_registry()`` populates the name -> class map by walking
``agentic_eval.core_evals.fi_evals.__all__``, and ``get_eval_class()`` looks up
by the *string* an eval type is identified by. The eval-type enums in
``agentic_eval.core_evals.fi_evals.eval_type`` hold exactly those strings as
their values (``FunctionEvalTypeId.CONTAINS_VALID_LINK == "ContainsValidLink"``).

So ``__all__`` is the de-facto registration contract: an evaluator class that
exists, is correct and is listed in ``eval_type.py`` but is *not* exported in
``__all__`` is silently absent from the registry. Nothing asserted that
correspondence, which made "someone forgot the export" a quiet runtime failure
rather than a red build. These tests close that.
"""

import pytest

from agentic_eval.core_evals.fi_evals import eval_type as eval_type_module
from evaluations.engine.registry import (
    get_eval_class,
    is_registered,
    list_registered,
)

# Every *EvalTypeId enum declared in eval_type.py, discovered rather than
# hard-coded so a newly added enum is covered automatically.
EVAL_TYPE_ENUMS = [
    getattr(eval_type_module, name)
    for name in sorted(dir(eval_type_module))
    if name.endswith("EvalTypeId") and isinstance(getattr(eval_type_module, name), type)
]

ALL_MEMBERS = [
    pytest.param(enum_cls, member, id=f"{enum_cls.__name__}.{member.name}")
    for enum_cls in EVAL_TYPE_ENUMS
    for member in enum_cls
]


class TestEnumRegistryContract:
    def test_enums_were_discovered(self):
        """Guards the discovery above — an empty parametrisation would pass silently."""
        assert EVAL_TYPE_ENUMS, "no *EvalTypeId enums found in eval_type.py"
        assert len(ALL_MEMBERS) > 50, f"only {len(ALL_MEMBERS)} enum members discovered"

    @pytest.mark.parametrize("enum_cls,member", ALL_MEMBERS)
    def test_every_enum_member_resolves(self, enum_cls, member):
        """The core contract: every declared eval type must resolve to something
        the engine can instantiate.

        A failure here almost always means the evaluator class was added to
        eval_type.py but not exported from fi_evals.__all__.

        Asserted as `callable` rather than `isinstance(..., type)` deliberately:
        EE-gated evaluators resolve to a real class in an enterprise build and to
        the `tfc.ee_stub._ee_stub` function in an OSS checkout. Both are callable
        and both are what `runner.run_eval()` does with the result — an
        `isinstance(..., type)` assertion would pass on EE and fail on OSS.
        See test_ee_gated_types_resolve_in_oss_builds below.
        """
        resolved = get_eval_class(member.value)
        assert callable(resolved), f"{member.value} resolved to {resolved!r}"

    @pytest.mark.parametrize("enum_cls,member", ALL_MEMBERS)
    def test_every_enum_member_reports_as_registered(self, enum_cls, member):
        assert is_registered(member.value)

    def test_enum_values_are_unique_across_all_enums(self):
        """Two enums mapping the same string would make lookups ambiguous."""
        seen = {}
        collisions = []
        for enum_cls in EVAL_TYPE_ENUMS:
            for member in enum_cls:
                key = member.value
                if key in seen:
                    collisions.append(f"{key}: {seen[key]} and {enum_cls.__name__}")
                seen[key] = enum_cls.__name__
        assert not collisions, "duplicate eval type ids: " + "; ".join(collisions)


class TestRegistryLookup:
    def test_unknown_eval_type_raises_value_error(self):
        with pytest.raises(ValueError) as exc_info:
            get_eval_class("NoSuchEvaluatorXYZ")
        assert "NoSuchEvaluatorXYZ" in str(exc_info.value)

    def test_is_registered_is_false_for_unknown_type(self):
        assert is_registered("NoSuchEvaluatorXYZ") is False

    def test_list_registered_is_non_empty_and_covers_the_enums(self):
        registered = set(list_registered())
        assert registered

        declared = {m.value for e in EVAL_TYPE_ENUMS for m in e}
        # The registry may be a superset — fi_evals exports helper/base classes
        # that no enum names. It must never be a strict subset.
        assert declared <= registered, (
            "declared eval types missing from the registry: "
            f"{sorted(declared - registered)}"
        )

    def test_lookup_is_stable_across_calls(self):
        """The registry is built once and memoised; repeat lookups agree."""
        name = ALL_MEMBERS[0].values[1].value
        assert get_eval_class(name) is get_eval_class(name)


class TestEnterpriseGatedEvalTypes:
    """EE-gated eval types still occupy the registry in an OSS checkout.

    `tfc/ee_loader.py` substitutes `tfc.ee_stub._ee_stub(name)` for symbols that
    live in the private `ee/` tree. The stub is a callable that raises
    FeatureUnavailable (HTTP 402) when invoked, so the eval type stays
    *addressable* — a caller gets a clean, catchable error instead of a
    KeyError or "Unknown evaluator type".

    Pinned because it is the reason the contract test above asserts `callable`
    rather than `isinstance(..., type)`, and because a change here would alter
    what OSS users see when they select an enterprise evaluator.
    """

    def _is_ee_stub(self, obj):
        return (
            callable(obj)
            and not isinstance(obj, type)
            and getattr(obj, "__module__", "") == "tfc.ee_stub"
        )

    def test_ee_gated_types_resolve_in_oss_builds(self):
        """Whether a type is a class or a stub depends on the build, but it
        must resolve either way."""
        for name in ("RankingEvaluator", "DeterministicEvaluator", "Groundedness"):
            resolved = get_eval_class(name)
            assert callable(resolved), f"{name} did not resolve"
            assert isinstance(resolved, type) or self._is_ee_stub(resolved), (
                f"{name} resolved to {resolved!r}, which is neither an evaluator "
                "class nor an ee stub"
            )

    def test_no_enum_member_resolves_to_none(self):
        """_build_registry() skips None entries, which would surface as
        'Unknown evaluator type' rather than a gated-feature error."""
        for enum_cls in EVAL_TYPE_ENUMS:
            for member in enum_cls:
                assert get_eval_class(member.value) is not None
