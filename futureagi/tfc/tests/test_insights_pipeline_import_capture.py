"""An Insights stage reports a broken EE pipeline import, but not its absence.

The activity runs through the production Sentry activity interceptor with a
real Sentry client that records the envelopes it would send.
"""

import builtins
import json
import logging
import sys
from types import SimpleNamespace

import pytest
import sentry_sdk
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from temporalio.exceptions import ApplicationError

from tfc import ee_loader
from tfc.capabilities import _bootstrap, service
from tfc.licensing.types import DeploymentFlavor
from tfc.temporal.common import sentry_interceptor
from tfc.temporal.insights import activities
from tfc.tests.sentry_capture import sentry_capture


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(sentry_interceptor, "_get_sentry_sdk", lambda: sentry_sdk)
    with sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        DedupeIntegration(),
    ) as transport:
        yield transport


def _import_raising(monkeypatch, error):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("ee.agenthub.insights"):
            raise error
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


async def _run_stage():
    async def stage():
        return activities._pipeline("prepare")

    async def execute(_):
        return await stage()

    interceptor = sentry_interceptor.SentryActivityInboundInterceptor(
        SimpleNamespace(execute_activity=execute)
    )
    with pytest.raises(ApplicationError) as raised:
        await interceptor.execute_activity(SimpleNamespace(fn=stage, args=[]))
    return raised.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing",
    ["ee", "ee.agenthub", "ee.agenthub.insights", "ee.agenthub.insights.pipeline"],
)
async def test_a_build_without_the_pipeline_is_a_quiet_refusal(
    transport, monkeypatch, missing
):
    monkeypatch.setattr(service, "is_configured", lambda: True)
    monkeypatch.setattr(service, "get_deployment_flavor", lambda: DeploymentFlavor.OSS)
    monkeypatch.delenv("EE_LICENSE_KEY", raising=False)
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.setattr(ee_loader, "has_ee", lambda module: False)
    _import_raising(
        monkeypatch, ModuleNotFoundError(f"No module named {missing!r}", name=missing)
    )

    error = await _run_stage()

    assert error.type == "CapabilityDenied"
    assert error.non_retryable
    assert error.message == activities.NOT_IN_BUILD
    assert transport.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cause",
    [
        # The pipeline is present but one of its own dependencies is not.
        ModuleNotFoundError(
            "No module named 'synthetic_insights_dep'", name="synthetic_insights_dep"
        ),
        ImportError("cannot import name 'prepare' from partially initialized module"),
        RuntimeError("synthetic pipeline import fault"),
    ],
    ids=["missing-dependency", "import-error", "module-raises"],
)
async def test_a_broken_pipeline_import_is_reported_once(transport, monkeypatch, cause):
    _import_raising(monkeypatch, cause)

    error = await _run_stage()

    assert error.type == "InsightsPipelineImportError"
    assert error.non_retryable
    assert error.message == activities.PIPELINE_IMPORT_FAILED
    assert error.__cause__ is cause
    assert len(transport.events) == 1
    exceptions = transport.events[0]["exception"]["values"]
    # Sentry lists the chain oldest first: the original error, then ours.
    assert [value["type"] for value in exceptions] == [
        type(cause).__name__,
        "ApplicationError",
    ]
    assert exceptions[0]["value"] == str(cause)
    assert activities.PIPELINE_IMPORT_FAILED in json.dumps(exceptions[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flavor", "usage", "insights"),
    [
        (DeploymentFlavor.CLOUD, False, False),
        (DeploymentFlavor.SELF_HOSTED_EE, False, False),
        (DeploymentFlavor.OSS, True, False),
        (DeploymentFlavor.OSS, False, True),
    ],
)
async def test_missing_stage_in_expected_install_is_reported(
    transport, monkeypatch, flavor, usage, insights
):
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    monkeypatch.delenv("EE_LICENSE_KEY", raising=False)
    monkeypatch.setattr(service, "is_configured", lambda: True)
    monkeypatch.setattr(service, "get_deployment_flavor", lambda: flavor)
    packages = {"ee.usage": usage, "ee.agenthub.insights": insights}
    monkeypatch.setattr(ee_loader, "has_ee", packages.__getitem__)
    cause = ModuleNotFoundError(
        "synthetic missing stage", name="ee.agenthub.insights.pipeline"
    )
    _import_raising(monkeypatch, cause)
    error = await _run_stage()
    assert error.type == "InsightsPipelineImportError"
    assert error.__cause__ is cause
    [event] = transport.events
    assert [v["type"] for v in event["exception"]["values"]] == [
        "ModuleNotFoundError",
        "ApplicationError",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("licensed", "usage", "flavor"),
    [
        (False, False, DeploymentFlavor.OSS),
        (True, False, DeploymentFlavor.OSS),  # Incomplete install: startup falls back.
        (True, True, DeploymentFlavor.SELF_HOSTED_EE),
    ],
    ids=["oss", "licensed-without-usage", "licensed-with-usage"],
)
async def test_self_hosted_startup_without_insights(
    transport, monkeypatch, licensed, usage, flavor
):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "false")
    monkeypatch.setenv("EE_LICENSE_KEY", "synthetic-license" if licensed else "")
    packages = {"ee.usage": usage, "ee.agenthub.insights": False}
    monkeypatch.setattr(_bootstrap, "has_ee", packages.__getitem__)
    monkeypatch.setattr(ee_loader, "has_ee", packages.__getitem__)
    monkeypatch.setitem(
        sys.modules,
        "ee.usage.deployment",
        SimpleNamespace(DeploymentMode=SimpleNamespace(is_cloud=lambda: False))
        if usage
        else None,
    )
    monkeypatch.setattr(_bootstrap, "_try_load_license_resolver", lambda: None)
    missing = "ee.agenthub.insights"
    cause = ModuleNotFoundError(f"No module named {missing!r}", name=missing)
    _import_raising(monkeypatch, cause)

    # Exercise startup selection and restore the process-wide wiring afterward.
    for attr in (
        "_configured",
        "_deployment_flavor",
        "_deployment_location",
        "_license_resolver",
        "_cloud_plan_resolver",
    ):
        monkeypatch.setattr(service, attr, getattr(service, attr))
    _bootstrap.wire_resolvers()
    assert service.is_configured()
    assert service.get_deployment_flavor() == flavor

    error = await _run_stage()
    assert error.non_retryable
    if not licensed:
        assert error.type == "CapabilityDenied"
        assert transport.events == []
        return

    assert error.type == "InsightsPipelineImportError"
    assert error.__cause__ is cause
    [event] = transport.events
    original, reported = event["exception"]["values"]
    assert original["type"] == "ModuleNotFoundError"
    assert original["value"] == str(error.__cause__)
    assert reported["type"] == "ApplicationError"
    assert activities.PIPELINE_IMPORT_FAILED in reported["value"]
