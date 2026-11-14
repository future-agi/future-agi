"""Registered explanation activity -> real log processing -> SDK envelopes.

Only data/model dependencies are synthetic. Keep the registered function, its
thread/heartbeat wrapper, structlog processors, stdlib logging integration and
Sentry interceptor intact; a direct before_send hint misses both regressions.
"""

import io
import json
import logging
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sentry_sdk
import structlog
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from temporalio.testing import ActivityEnvironment

from tfc.logging.sentry import _event_scrubber
from tfc.tests.sentry_capture import sentry_capture

SUMMARY_LOGGER = "model_hub.utils.eval_reasons"
ACTIVITY_LOGGER = "temporalio.activity"
ACTIVITY_NAME = "get_explanation_summary"
DENIAL_FIELDS = {
    "event": "explanation_summary_denied",
    "reason": "feature_unavailable",
    "outcome": "handled",
    "activity_name": ACTIVITY_NAME,
}


@pytest.fixture(params=["django", "temporal"])
def real_logging(request, monkeypatch, tmp_path):
    from model_hub.utils import eval_reasons
    from tfc.logging.config import configure_structlog, get_logging_config
    from tfc.logging.temporal import configure_temporal_logging

    previous_config = structlog.get_config()
    monkeypatch.setenv("ENV_TYPE", "production")
    # Use the real Django formatter even for Temporal's pre-rendered JSON, as
    # start_temporal_worker does after Django has configured stdlib logging.
    formatter_options = get_logging_config(str(tmp_path))["formatters"]["structured"]
    formatter = formatter_options.pop("()")(**formatter_options)
    records = []
    stream = io.StringIO()

    class RecordingHandler(logging.StreamHandler):
        def emit(self, record):
            records.append(record)
            super().emit(record)

    handler = RecordingHandler(stream)
    handler.setFormatter(formatter)
    for name in (SUMMARY_LOGGER, ACTIVITY_LOGGER):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", [handler])
        monkeypatch.setattr(logger, "propagate", False)
        monkeypatch.setattr(logger, "level", logging.DEBUG)
        monkeypatch.setattr(logger, "disabled", False)

    try:
        if request.param == "django":
            configure_structlog(cache_logger_on_first_use=False)
        else:
            configure_temporal_logging()
        # Do not reuse a proxy cached under a previous test's processors.
        monkeypatch.setattr(
            eval_reasons, "logger", structlog.get_logger(SUMMARY_LOGGER)
        )
        yield SimpleNamespace(mode=request.param, records=records, stream=stream)
    finally:
        structlog.configure(**previous_config)
        handler.close()


@pytest.fixture
def registered_summary(monkeypatch):
    from model_hub.models.choices import EvalExplanationSummaryStatus
    from model_hub.utils import eval_reasons
    from tfc.temporal.drop_in import decorator

    dataset = SimpleNamespace(
        id="synthetic-dataset",
        eval_reason_status=EvalExplanationSummaryStatus.COMPLETED,
        eval_reason_last_updated=None,
        eval_reasons={"previous": ["synthetic summary"]},
    )
    saved = []

    def save(*, update_fields):
        saved.append((dataset.eval_reason_status, update_fields))

    dataset.save = MagicMock(side_effect=save)
    dataset_get = MagicMock(return_value=dataset)
    monkeypatch.setattr(
        eval_reasons,
        "Dataset",
        SimpleNamespace(
            objects=SimpleNamespace(get=dataset_get),
            DoesNotExist=eval_reasons.Dataset.DoesNotExist,
        ),
    )
    row_filter = MagicMock()
    row_filter.return_value.count.return_value = (
        eval_reasons.MIN_ROWS_FOR_CRITICAL_ISSUES
    )
    monkeypatch.setattr(
        eval_reasons, "Row", SimpleNamespace(objects=SimpleNamespace(filter=row_filter))
    )
    collect = MagicMock(return_value={})
    monkeypatch.setattr(eval_reasons, "get_eval_reasons", collect)

    metadata = decorator.get_activity_by_name(ACTIVITY_NAME)
    assert metadata["func"] is eval_reasons.get_explanation_summary._original_func
    assert metadata["max_retries"] is None
    assert metadata["retry_delay"] is None
    # Build exactly what the worker registers, not a synthetic decorated task or
    # a direct call to _original_func. Restore wrapper caching after this test.
    monkeypatch.setattr(decorator, "_ACTIVITY_WRAPPERS", {})
    activities = decorator.get_temporal_activities(queue="tasks_l")
    registered = decorator._ACTIVITY_WRAPPERS[ACTIVITY_NAME]
    assert registered in activities
    return SimpleNamespace(
        fn=registered, dataset=dataset, saved=saved, collect=collect, get=dataset_get
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "ee_stub",
        "plan_feature_missing",
        "resolver_unavailable",
        "legacy_lookup_outage",
        "unrelated_fault",
        "http_402",
        "lookalike_type",
    ],
)
async def test_registered_explanation_activity_capture(
    source, real_logging, registered_summary, monkeypatch
):
    from rest_framework.exceptions import APIException

    from model_hub.models.choices import EvalExplanationSummaryStatus
    from model_hub.utils import eval_reasons
    from tfc.capabilities import service
    from tfc.ee_gating import FeatureUnavailable, check_ee_feature
    from tfc.ee_stub import _ee_stub
    from tfc.temporal.common import sentry_interceptor

    decision = service.CapabilityDecision(
        allowed=False,
        feature_id="insights",
        reason_code=(
            "RESOLVER_UNAVAILABLE"
            if source == "resolver_unavailable"
            else "PLAN_FEATURE_MISSING"
        ),
    )
    monkeypatch.setattr(service, "is_configured", lambda: True)
    monkeypatch.setattr(service, "check", lambda *args, **kwargs: decision)
    emitted = []

    def construct_agent():
        try:
            if source == "ee_stub":
                _ee_stub("ExplanationAgent")()
            elif source in ("plan_feature_missing", "resolver_unavailable"):
                check_ee_feature("insights")
            elif source == "legacy_lookup_outage":
                raise FeatureUnavailable(
                    "ExplanationAgent",
                    detail="Feature entitlement service is unavailable.",
                )
            elif source == "http_402":
                error = APIException("synthetic refusal", code="ENTITLEMENT_DENIED")
                error.status_code = 402
                raise error
            elif source == "lookalike_type":
                lookalike = type(
                    "FeatureUnavailable", (Exception,), {"status_code": 402}
                )
                raise lookalike("synthetic refusal")
            else:
                # Message text that resembles a denial is still a genuine fault.
                raise RuntimeError(
                    "FeatureUnavailable: Upgrade your plan. synthetic fault"
                )
        except Exception as error:
            emitted.append(error)
            raise
        raise AssertionError("The synthetic dependency must fail")

    constructor = MagicMock(side_effect=construct_agent)
    monkeypatch.setattr(eval_reasons, "ExplanationAgent", constructor)
    monkeypatch.setattr(sentry_interceptor, "_get_sentry_sdk", lambda: sentry_sdk)
    environment = ActivityEnvironment()
    environment.info = replace(environment.info, activity_type=ACTIVITY_NAME)

    async def execute(activity_input):
        return await environment.run(activity_input.fn, *activity_input.args)

    interceptor = sentry_interceptor.SentryActivityInboundInterceptor(
        SimpleNamespace(execute_activity=execute)
    )
    activity_input = SimpleNamespace(
        fn=registered_summary.fn,
        args=[{"args": [registered_summary.dataset.id], "kwargs": {}}],
    )
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        DedupeIntegration(),
        attach_stacktrace=True,
        event_scrubber=_event_scrubber(),
        max_value_length=8192,
    ) as transport:
        with pytest.raises(Exception) as raised:
            await interceptor.execute_activity(activity_input)
        assert raised.value is emitted[0]
        constructor.assert_called_once_with()
        registered_summary.get.assert_called_once_with(id="synthetic-dataset")
        registered_summary.collect.assert_called_once_with(
            dataset_id="synthetic-dataset"
        )
        assert registered_summary.saved == [
            (EvalExplanationSummaryStatus.RUNNING, ["eval_reason_status"]),
            (
                EvalExplanationSummaryStatus.FAILED,
                ["eval_reason_status", "eval_reason_last_updated"],
            ),
        ]
        assert registered_summary.dataset.eval_reasons == {
            "previous": ["synthetic summary"]
        }
        assert registered_summary.dataset.eval_reason_last_updated is not None

        errors = [r for r in real_logging.records if r.levelno >= logging.ERROR]
        assert [r.name for r in errors] == [SUMMARY_LOGGER, ACTIVITY_LOGGER]
        inner, outer = errors
        # This is the actual failure mode: neither log hint has the live type.
        assert inner.exc_info is None
        assert outer.exc_info is None
        payload = inner.msg
        if real_logging.mode == "temporal":
            assert isinstance(payload, str)
            payload = json.loads(payload)
        else:
            assert isinstance(payload, dict)
        assert type(raised.value).__name__ in payload["exception"]
        # Both records also made it through a real stdlib ProcessorFormatter.
        assert SUMMARY_LOGGER in real_logging.stream.getvalue()
        assert ACTIVITY_LOGGER in real_logging.stream.getvalue()

        if source in ("ee_stub", "plan_feature_missing"):
            for key, value in DENIAL_FIELDS.items():
                assert payload[key] == value
                assert getattr(outer, key) == value
            assert raised.value.status_code == 402
            if source == "plan_feature_missing":
                assert raised.value.reason_code == "PLAN_FEATURE_MISSING"
            assert transport.events == []
            sentry_sdk.capture_exception(RuntimeError("synthetic subsequent fault"))
            [event] = transport.events
            assert event["exception"]["values"][-1]["type"] == "RuntimeError"
            categories = {b.get("category") for b in event["breadcrumbs"]["values"]}
            assert {SUMMARY_LOGGER, ACTIVITY_LOGGER} <= categories
        else:
            assert payload.get("event") != DENIAL_FIELDS["event"]
            assert not hasattr(outer, "outcome")
            # Both logging emissions survive, as does the propagated exception
            # captured by the real interceptor (not merely a lookalike message).
            assert [event.get("logger") for event in transport.events] == [
                SUMMARY_LOGGER,
                ACTIVITY_LOGGER,
                None,
            ]
            assert transport.events[-1]["exception"]["values"][-1]["type"] == (
                type(raised.value).__name__
            )
            if source == "resolver_unavailable":
                assert raised.value.reason_code == "RESOLVER_UNAVAILABLE"


@pytest.mark.parametrize(
    "case",
    [
        "other_logger",
        "other_activity",
        "unknown_reason",
        "unhandled",
        "direct_capture",
        "exception",
    ],
)
def test_explanation_denial_classification_is_source_bound(case):
    fields = dict(DENIAL_FIELDS)
    logger_name = ACTIVITY_LOGGER
    if case == "other_logger":
        logger_name = f"{ACTIVITY_LOGGER}.other"
    elif case == "other_activity":
        fields["activity_name"] = "other_activity"
    elif case == "unknown_reason":
        fields["reason"] = "RESOLVER_UNAVAILABLE"
    elif case == "unhandled":
        fields["outcome"] = "unhandled"

    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        DedupeIntegration(),
        attach_stacktrace=True,
        event_scrubber=_event_scrubber(),
        max_value_length=8192,
    ) as transport:
        if case == "direct_capture":
            sentry_sdk.capture_event(
                {"logger": logger_name, "level": "error", "message": json.dumps(fields)}
            )
        elif case == "exception":
            try:
                raise RuntimeError("synthetic classified handler fault")
            except RuntimeError:
                logging.getLogger(logger_name).exception("synthetic fault", extra=fields)
        else:
            logging.getLogger(logger_name).error("synthetic fault", extra=fields)
        assert len(transport.events) == 1
