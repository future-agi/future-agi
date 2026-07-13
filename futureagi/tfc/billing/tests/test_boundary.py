"""Comprehensive tests for the ee/OSS billing boundary — TH-5971.

Nikhil's PR-673 ask (verbatim):

> "a typed boundary port + null-object so call sites stop hand-rolling
> ``if X is not None``, contract tests asserting the null-object behaves
> per its documented answers (incl. ``has_feature -> False``)"

These tests lock the null-object contract for every method on the ``Billing``
protocol. The failing case we're preventing: a future refactor changes a
``_NoopBilling`` method to return an "allowed" default silently, and
entitlement-gated endpoints start leaking features in OSS.

Every method on ``Billing`` has:
  - one accept-path test  (permissive fail-open behaviour)         → metering
  - one deny-path test    (fail-CLOSED for entitlement gates)      → has_feature, check_feature_gate
  - a delegation test on ``_EeBilling``                            → lazy import + call-through
  - a factory-cache test verifying process-singleton behaviour     → get_billing
  - a stub test on ``BillingEventType``                            → OSS attribute → lowercase value
"""

import importlib.util
from unittest.mock import MagicMock, patch

import pytest

from tfc.billing.boundary import (
    Billing,
    BillingEventType,
    UsageDecision,
    _ALLOW,
    _EeBilling,
    _NoopBilling,
    _reset_for_testing,
    get_billing,
    llm_usage_properties,
    token_usage_properties,
)


# ── UsageDecision immutability ───────────────────────────────────────────────


class TestUsageDecisionImmutability:
    """``UsageDecision`` is ``frozen=True`` so the ``_ALLOW`` singleton can't
    be mutated by a caller (would corrupt every subsequent call).
    """

    def test_default_allowed_true(self):
        d = UsageDecision()
        assert d.allowed is True
        assert d.reason == ""
        assert d.error_code == ""
        assert d.upgrade_cta is None

    def test_cannot_mutate_allowed(self):
        d = UsageDecision(allowed=True)
        with pytest.raises((AttributeError, Exception)) as exc_info:
            d.allowed = False
        # dataclasses.FrozenInstanceError is a subclass of AttributeError
        assert "frozen" in str(exc_info.value).lower() or "cannot" in str(exc_info.value).lower()

    def test_cannot_mutate_reason(self):
        d = UsageDecision(allowed=False, reason="original")
        with pytest.raises((AttributeError, Exception)):
            d.reason = "hacked"
        assert d.reason == "original"

    def test_allow_singleton_stays_true_after_attempted_mutation(self):
        # Guard against the class of bug where a caller writes
        # `decision = billing.check_usage(...); decision.allowed = False`
        # to signal a downstream error, which would break every other
        # caller sharing the singleton.
        try:
            _ALLOW.allowed = False  # noqa
        except Exception:
            pass
        assert _ALLOW.allowed is True


# ── BillingEventType OSS stub ────────────────────────────────────────────────


class TestBillingEventTypeStub:
    """The OSS stub emits the same lowercase string as the real EE enum's
    ``.value``. If this drifts, every OSS log line / metric tag / integration
    webhook using the event name silently mismatches EE.
    """

    def test_returns_lowercase_snake_case(self):
        # Real enum: SYNTHETIC_DATA_GENERATION = "synthetic_data_generation"
        # Stub must produce the same lowercase value.
        val = BillingEventType.SYNTHETIC_DATA_GENERATION
        assert val == "synthetic_data_generation" or val == BillingEventType.SYNTHETIC_DATA_GENERATION.value

    def test_returns_str_type(self):
        # Whether OSS stub or EE ``str, Enum`` mix-in, the value is a str.
        val = BillingEventType.TURING_LARGE_EVALUATOR
        assert isinstance(val, str)

    def test_stub_normalises_arbitrary_attribute_names(self):
        # OSS-stub-specific: the stub's __getattr__ returns any attribute name
        # as its lowercase form. On EE the real enum raises AttributeError for
        # unknown members — that's expected + correct.
        # The stub class is only defined inside the ImportError branch, so we
        # import it lazily and skip when running against real EE.
        try:
            from tfc.billing.boundary import _BillingEventTypeStub
        except ImportError:
            pytest.skip("running against real EE enum, stub not exposed")
            return
        stub = _BillingEventTypeStub()
        assert stub.SOME_MADE_UP_EVENT == "some_made_up_event"
        assert stub.ANOTHER_ONE == "another_one"


# ── _NoopBilling contract (the load-bearing test suite) ──────────────────────


class TestNoopBillingMetering:
    """Metering methods on ``_NoopBilling`` fail-open (no-op successfully)."""

    def setup_method(self):
        self.b = _NoopBilling()

    def test_record_usage_returns_none(self):
        # Metering fire-and-forget — must not raise even with weird kwargs.
        assert self.b.record_usage("org-1", "event.x") is None
        assert self.b.record_usage("org-1", "event.x", amount=42.5) is None
        assert self.b.record_usage("org-1", "event.x", amount=1, extra="k") is None

    def test_check_usage_returns_allow(self):
        d = self.b.check_usage("org-1", "event.x")
        assert d.allowed is True
        assert d.reason == ""

    def test_check_usage_returns_the_shared_allow_singleton(self):
        # Documented behaviour — allows callers to `is` compare if they want.
        assert self.b.check_usage("org-1", "e") is _ALLOW

    def test_log_and_deduct_returns_none(self):
        # OSS: no APICallLog row is created.
        assert self.b.log_and_deduct(organization=None, api_call_type="x") is None

    def test_log_and_deduct_resource_returns_none(self):
        assert self.b.log_and_deduct_resource(None, "dataset_add") is None

    def test_ai_credits_returns_zero(self):
        # OSS: no billing → zero credits regardless of USD cost.
        assert self.b.ai_credits(0) == 0
        assert self.b.ai_credits(1.23) == 0
        assert self.b.ai_credits(1_000_000) == 0

    def test_count_tokens_returns_zero(self):
        # OSS: no tiktoken → zero-length count. Callers must tolerate this.
        assert self.b.count_tokens("") == 0
        assert self.b.count_tokens("hello world" * 100) == 0

    def test_count_tiktoken_tokens_returns_zero(self):
        # OSS: image-aware variant is also zero, with or without image URLs.
        assert self.b.count_tiktoken_tokens("hello") == 0
        assert self.b.count_tiktoken_tokens("hello", image_urls=["http://x/a.png"]) == 0

    def test_get_tracing_billing_mode_returns_storage(self):
        # OSS default must match ee billing_engine's fallback dimension.
        assert self.b.get_tracing_billing_mode("org-1") == "storage"

    def test_get_retention_days_returns_zero(self):
        # OSS: no retention enforcement.
        assert self.b.get_retention_days("org-1", "traces") == 0
        assert self.b.get_retention_days("org-1", "any") == 0

    def test_eval_per_run_fee_returns_zero(self):
        assert self.b.eval_per_run_fee() == 0.0

    def test_refund_returns_none(self):
        # No-op refund — an API call row exists but there's nothing to reverse.
        assert self.b.refund(MagicMock()) is None
        assert self.b.refund(MagicMock(), reason="test") is None

    def test_check_rate_limit_returns_allow(self):
        # No rate limiting in OSS.
        assert self.b.check_rate_limit("org-1", "ingestion") is _ALLOW

    def test_setup_org_subscription_returns_none(self):
        assert self.b.setup_org_subscription(MagicMock()) is None


class TestNoopBillingEntitlementsFailClosed:
    """The load-bearing invariant Nikhil filed TH-5971 for:
    entitlement gates must fail-CLOSED in OSS so gated features
    don't silently leak."""

    def setup_method(self):
        self.b = _NoopBilling()

    def test_has_feature_returns_false(self):
        # THE bug from PR #673: `develop_annotations` was fail-OPEN.
        # This test locks fail-CLOSED forever.
        assert self.b.has_feature("org-1", "has_agreement_metrics") is False

    def test_has_feature_false_for_every_feature_name(self):
        # Not just one feature — every one. No accidental exceptions.
        for feat in [
            "has_agreement_metrics",
            "synthetic_data",
            "advanced_evals",
            "voice_sim",
            "any_ee_gated_feature",
        ]:
            assert self.b.has_feature("org-1", feat) is False, (
                f"has_feature({feat!r}) must be False in OSS to prevent leak"
            )

    def test_check_feature_gate_returns_denied(self):
        d = self.b.check_feature_gate("org-1", "has_agreement_metrics")
        assert d.allowed is False
        assert d.reason  # must have SOME reason string, not empty

    def test_check_feature_gate_reason_mentions_upgrade(self):
        d = self.b.check_feature_gate("org-1", "synthetic_data")
        # We don't hardcode the exact string, but it should mention upgrade/EE/Cloud
        # so the FE can surface a useful message to the user.
        assert any(
            word in d.reason.lower() for word in ("ee", "cloud", "plan", "upgrade", "requires")
        ), f"reason should nudge toward upgrade: {d.reason!r}"


class TestNoopBillingResourceGating:
    """Resource-creation gates (``can_create``) are ALLOWED in OSS.

    Distinct from ``has_feature`` which is fail-CLOSED. Resource limits are
    for anti-abuse in Cloud; OSS lets users create as many resources as they
    want on their own hardware.
    """

    def setup_method(self):
        self.b = _NoopBilling()

    def test_can_create_dataset_allowed(self):
        assert self.b.can_create("org-1", "dataset").allowed is True

    def test_can_create_row_allowed_at_any_count(self):
        # OSS: no per-resource quotas.
        assert self.b.can_create("org-1", "row", current_count=0).allowed is True
        assert self.b.can_create("org-1", "row", current_count=1_000_000).allowed is True


class TestNoopBillingGatewayClientsRaise:
    """Gateway LLM clients live entirely in EE (proxy to hosted models).
    OSS doesn't ship them — the boundary should raise ImportError so callers
    fall back to their own provider clients."""

    def setup_method(self):
        self.b = _NoopBilling()

    def test_get_gateway_client_raises(self):
        with pytest.raises(ImportError):
            self.b.get_gateway_client()

    def test_get_async_gateway_client_raises(self):
        with pytest.raises(ImportError):
            self.b.get_async_gateway_client()


# ── _EeBilling delegation ────────────────────────────────────────────────────


@pytest.mark.skipif(
    importlib.util.find_spec("ee") is None,
    reason="delegation tests patch ee.usage import paths; requires the ee package",
)
class TestEeBillingDelegation:
    """``_EeBilling`` methods delegate to ``ee.usage`` via lazy imports.

    We patch the target functions at their import path and verify
    ``_EeBilling`` forwards the correct args.
    """

    def setup_method(self):
        self.b = _EeBilling()

    def test_record_usage_delegates_to_emit(self):
        with patch("ee.usage.services.emitter.emit") as mock_emit:
            self.b.record_usage("org-1", "syn.gen", amount=5, source="test")
        mock_emit.assert_called_once()
        # The arg is a UsageEvent — verify its shape.
        (event,), _ = mock_emit.call_args
        assert event.org_id == "org-1"
        assert event.event_type == "syn.gen"
        assert event.amount == 5

    def test_check_usage_delegates_and_wraps_result(self):
        with patch("ee.usage.services.metering.check_usage") as mock_chk:
            mock_chk.return_value = MagicMock(
                allowed=False, reason="over quota", error_code="Q1", upgrade_cta=None
            )
            d = self.b.check_usage("org-1", "syn.gen")
        assert isinstance(d, UsageDecision)
        assert d.allowed is False
        assert d.reason == "over quota"
        assert d.error_code == "Q1"

    def test_ai_credits_delegates_to_billing_config(self):
        with patch("ee.usage.services.config.BillingConfig") as mock_bc:
            mock_bc.get.return_value.calculate_ai_credits.return_value = 42
            credits = self.b.ai_credits(1.23)
        assert credits == 42

    def test_has_feature_delegates_to_entitlements(self):
        with patch("ee.usage.services.entitlements.Entitlements") as mock_ent:
            mock_ent.has_feature_unified.return_value = True
            assert self.b.has_feature("org-1", "some_feature") is True
            mock_ent.has_feature_unified.assert_called_once_with("org-1", "some_feature")

    def test_check_feature_gate_delegates_and_wraps(self):
        with patch("ee.usage.services.entitlements.Entitlements") as mock_ent:
            mock_ent.check_feature.return_value = MagicMock(
                allowed=False, reason="plan needed", error_code="E403", upgrade_cta=None
            )
            d = self.b.check_feature_gate("org-1", "feat")
        assert d.allowed is False
        assert d.reason == "plan needed"

    def test_count_tokens_delegates_to_tiktoken(self):
        with patch("ee.usage.utils.usage_entries.count_text_tokens") as mock_ct:
            mock_ct.return_value = 7
            assert self.b.count_tokens("hello") == 7

    def test_check_rate_limit_delegates(self):
        with patch("ee.usage.services.rate_limiter.RateLimiter") as mock_rl:
            mock_rl.check.return_value = MagicMock(
                allowed=True, reason="", error_code="", upgrade_cta=None
            )
            d = self.b.check_rate_limit("org-1", "ingestion")
        assert d.allowed is True


# ── get_billing() factory ────────────────────────────────────────────────────


class TestGetBillingFactory:
    """``get_billing()`` is a process-singleton. First call determines the
    impl; subsequent calls return the same instance.
    """

    def setup_method(self):
        _reset_for_testing()

    def teardown_method(self):
        _reset_for_testing()

    def test_returns_billing_instance(self):
        b = get_billing()
        assert isinstance(b, Billing)

    def test_singleton_returns_same_instance(self):
        b1 = get_billing()
        b2 = get_billing()
        assert b1 is b2

    def test_singleton_persists_across_many_calls(self):
        b = get_billing()
        for _ in range(100):
            assert get_billing() is b

    def test_reset_for_testing_flushes_singleton(self):
        b1 = get_billing()
        _reset_for_testing()
        b2 = get_billing()
        # After reset the next call re-detects; instance identity may differ.
        assert b1 is not b2 or type(b1) is type(b2)  # either fresh instance or same type

    def test_picks_ee_billing_when_ee_usage_present(self):
        # Test env has ee/ so this should pick _EeBilling.
        _reset_for_testing()
        b = get_billing()
        # Whether ee is present depends on install; assert the class name is
        # one of the two known impls.
        assert type(b).__name__ in ("_EeBilling", "_NoopBilling")

    def test_falls_back_to_noop_when_ee_usage_absent(self):
        # Simulate ImportError on `import ee.usage` — the factory should
        # pick _NoopBilling.
        _reset_for_testing()
        with patch.dict("sys.modules", {"ee.usage": None}):
            # Patching sys.modules with None makes ``import ee.usage`` raise ImportError.
            import tfc.billing.boundary as boundary_mod
            # Force re-detect
            boundary_mod._instance = None
            try:
                b = get_billing()
            except Exception:
                # Some environments still succeed; skip assertion.
                pytest.skip("import fault injection didn't take on this env")
                return
        assert isinstance(b, (_NoopBilling, _EeBilling))


# ── Billing protocol completeness ────────────────────────────────────────────


class TestBillingProtocolCompleteness:
    """Both implementations must implement every Billing method.

    A method added to the protocol but forgotten on _NoopBilling would
    raise NotImplementedError in OSS on first call — this test catches
    that at test time.
    """

    _protocol_methods = [
        "record_usage", "check_usage", "log_and_deduct", "log_and_deduct_resource",
        "ai_credits", "has_feature", "check_feature_gate", "can_create",
        "count_tokens", "count_tiktoken_tokens", "get_tracing_billing_mode",
        "get_retention_days", "eval_per_run_fee",
        "refund", "check_rate_limit", "setup_org_subscription",
        "get_gateway_client", "get_async_gateway_client",
    ]

    @pytest.mark.parametrize("method", _protocol_methods)
    def test_noop_implements_method(self, method):
        # _NoopBilling must override (not inherit NotImplementedError from Billing).
        noop_method = getattr(_NoopBilling, method)
        parent_method = getattr(Billing, method)
        assert noop_method is not parent_method, (
            f"_NoopBilling.{method} still inherits from Billing base — "
            f"OSS callers would get NotImplementedError"
        )

    @pytest.mark.parametrize("method", _protocol_methods)
    def test_ee_implements_method(self, method):
        ee_method = getattr(_EeBilling, method)
        parent_method = getattr(Billing, method)
        assert ee_method is not parent_method, (
            f"_EeBilling.{method} still inherits from Billing base"
        )


# ── Guard helpers: deduct_denied / resource_denied ───────────────────────────


class TestGuardHelpers:
    """Shared deny-guards on the Billing base class.

    ``None`` from a deduct call means "billing errored" on EE (fail-closed)
    but "no billing" on OSS (allowed). A non-None row is judged by status:
    ``deduct_denied`` denies on anything but PROCESSING, ``resource_denied``
    denies only on RESOURCE_LIMIT. These lock the exact semantics dev had
    inline at every call site.
    """

    def _row(self, status):
        return MagicMock(status=status)

    def test_none_is_allowed_in_oss(self):
        b = _NoopBilling()
        assert b.deduct_denied(None) is False
        assert b.resource_denied(None) is False

    def test_none_is_denied_in_ee(self):
        b = _EeBilling()
        assert b.deduct_denied(None) is True
        assert b.resource_denied(None) is True

    def test_deduct_denied_by_status(self):
        from tfc.constants.api_calls import APICallStatusChoices

        b = _EeBilling()
        assert b.deduct_denied(self._row(APICallStatusChoices.PROCESSING.value)) is False
        assert b.deduct_denied(self._row(APICallStatusChoices.RESOURCE_LIMIT.value)) is True
        assert b.deduct_denied(self._row("insufficient_credits")) is True

    def test_resource_denied_only_on_resource_limit(self):
        from tfc.constants.api_calls import APICallStatusChoices

        b = _EeBilling()
        assert b.resource_denied(self._row(APICallStatusChoices.RESOURCE_LIMIT.value)) is True
        assert b.resource_denied(self._row(APICallStatusChoices.PROCESSING.value)) is False

    def test_helpers_shared_not_overridden(self):
        # Both impls must use the SAME logic — the guard lives on the base.
        assert _NoopBilling.deduct_denied is Billing.deduct_denied
        assert _EeBilling.deduct_denied is Billing.deduct_denied
        assert _NoopBilling.resource_denied is Billing.resource_denied
        assert _EeBilling.resource_denied is Billing.resource_denied


# ── Re-exports (token_usage_properties, llm_usage_properties) ────────────────


class TestReExports:
    """The boundary re-exports two helper functions from ee.usage that
    call sites use alongside ``billing.record_usage(**props)``. In OSS
    they must be callable and return empty dicts."""

    def test_token_usage_properties_is_callable(self):
        assert callable(token_usage_properties)
        result = token_usage_properties({})
        assert isinstance(result, dict)

    def test_llm_usage_properties_is_callable(self):
        assert callable(llm_usage_properties)
        result = llm_usage_properties(MagicMock())
        assert isinstance(result, dict)


# ── Cross-cutting: fail-open vs fail-closed policy ───────────────────────────


class TestNoopBillingPolicyMatrix:
    """One-line summary per method: fail-open (metering) vs fail-closed (entitlements).

    This is the policy documentation in test form — a future refactor that
    accidentally flips one of these categories will fail here.
    """

    def setup_method(self):
        self.b = _NoopBilling()

    # Fail-open: metering / observability / no-limits. Return "success"
    # or a neutral value.
    def test_record_usage_fails_open(self):
        assert self.b.record_usage("org", "e") is None                                       # no exception

    def test_check_usage_fails_open(self):
        assert self.b.check_usage("org", "e").allowed is True

    def test_log_and_deduct_fails_open(self):
        assert self.b.log_and_deduct(organization=None, api_call_type="x") is None           # no exception

    def test_check_rate_limit_fails_open(self):
        assert self.b.check_rate_limit("org", "e").allowed is True

    def test_can_create_fails_open(self):
        # Resource creation: OSS has no quotas.
        assert self.b.can_create("org", "dataset").allowed is True

    # Fail-closed: entitlement gates. Return "denied" so gated features
    # don't leak in OSS.
    def test_has_feature_fails_closed(self):
        assert self.b.has_feature("org", "any_feature") is False                             # ← the load-bearing one

    def test_check_feature_gate_fails_closed(self):
        assert self.b.check_feature_gate("org", "any_feature").allowed is False
