"""The two monitors every deployment's or Console agent's Observe project starts with
(SPEC 2.7, decision 3; learning-service PLAN P1-08).

One concern: what the defaults are. Creating and adopting them is ``monitors.py``.

A deployment's pair watches production runs only and leaves check runs out:

- ``omega.deployment.environment = production``: the EE relay overwrites it on
  every span, so a preview revision never trips a production alert.
- ``omega.run.source != check``: a check case that fails on purpose is not an
  incident. Span attribute negation in ClickHouse needs the key present
  (``ClickHouseFilterBuilder._span_attr_inner``), so a span with no run source
  is not counted. EE sets ``OMEGA_RUN_SOURCE`` on every deployed container
  (``api`` for traffic, ``check`` for check runs; EE
  ``project-deployments/env/identity.ts``), so every deployed span has one.

A Console agent's pair has no span filter. Its project holds that one agent's
Console runs and nothing else, and Console spans carry neither attribute, so either
filter would leave the pair counting nothing.
"""

from dataclasses import dataclass

from tracer.models.monitor import (
    ComparisonOperatorChoices,
    MonitorMetricTypeChoices,
    ThresholdCalculationMethodChoices,
)
from tracer.models.omega_deployment_link import ObserveLinkKind

PROJECT_PREFIX = "deploy/"
PRODUCTION = "production"
CHECK_SOURCE = "check"

ERRORS_SPIKE = "errors_spike"
DAILY_TOKENS = "daily_tokens"

# Decision 3's default: daily tokens over 1,000,000 a day. Today's monitor
# counts tokens, not dollars (``daily_tokens_spent``).
DAILY_TOKENS_LIMIT = 1_000_000.0

# SPEC 2.7 names the mode (percentage change against the monitor's own recent
# history, every 15 minutes) but no percentage. The monitor compares each
# 15-minute count with the average 15-minute count of the last 7 days and
# alerts above that average * (1 + value / 100): 200 is more than 3 times the
# average (critical), 100 more than twice it (warning). The owner can change
# both in Observe -> Alerts.
ERRORS_SPIKE_CRITICAL_PERCENT = 200.0
ERRORS_SPIKE_WARNING_PERCENT = 100.0
ERRORS_SPIKE_FREQUENCY_MINUTES = 15
DAILY_TOKENS_FREQUENCY_MINUTES = 60
# The monitor model's own default history window (one week).
HISTORY_MINUTES = 60 * 24 * 7


def project_name_for(deployment_name: str) -> str:
    """The Observe project a deployment's traces land in (EE ``observeProjectName``)."""
    return f"{PROJECT_PREFIX}{deployment_name}"


def _span_attribute_row(key: str, op: str, value: str) -> dict:
    """One row in the trace list's own shape, so a fired alert links to it (SPEC 5.5)."""
    return {
        "column_id": key,
        "property_id": f"custom_attribute:{key}",
        "display_name": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


def production_without_checks_filters() -> dict:
    """The monitor filters both defaults share."""
    return {
        "span_attributes_filters": [
            _span_attribute_row("omega.deployment.environment", "equals", PRODUCTION),
            _span_attribute_row("omega.run.source", "not_equals", CHECK_SOURCE),
        ]
    }


@dataclass(frozen=True)
class MonitorSpec:
    kind: str
    name: str
    metric_type: str
    threshold_type: str
    threshold_operator: str
    critical_threshold_value: float
    warning_threshold_value: float | None
    alert_frequency: int
    auto_threshold_time_window: int


DEFAULT_MONITORS = (
    MonitorSpec(
        kind=ERRORS_SPIKE,
        name="Errors spike (production)",
        metric_type=MonitorMetricTypeChoices.COUNT_OF_ERRORS.value,
        threshold_type=ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE.value,
        threshold_operator=ComparisonOperatorChoices.GREATER_THAN.value,
        critical_threshold_value=ERRORS_SPIKE_CRITICAL_PERCENT,
        warning_threshold_value=ERRORS_SPIKE_WARNING_PERCENT,
        alert_frequency=ERRORS_SPIKE_FREQUENCY_MINUTES,
        auto_threshold_time_window=HISTORY_MINUTES,
    ),
    MonitorSpec(
        kind=DAILY_TOKENS,
        name="Daily tokens over 1,000,000 (production)",
        metric_type=MonitorMetricTypeChoices.DAILY_TOKENS_SPENT.value,
        threshold_type=ThresholdCalculationMethodChoices.STATIC.value,
        threshold_operator=ComparisonOperatorChoices.GREATER_THAN.value,
        critical_threshold_value=DAILY_TOKENS_LIMIT,
        warning_threshold_value=None,
        alert_frequency=DAILY_TOKENS_FREQUENCY_MINUTES,
        auto_threshold_time_window=HISTORY_MINUTES,
    ),
)

AGENT_DEFAULT_MONITORS = (
    MonitorSpec(
        kind=ERRORS_SPIKE,
        name="Errors spike",
        metric_type=MonitorMetricTypeChoices.COUNT_OF_ERRORS.value,
        threshold_type=ThresholdCalculationMethodChoices.PERCENTAGE_CHANGE.value,
        threshold_operator=ComparisonOperatorChoices.GREATER_THAN.value,
        critical_threshold_value=ERRORS_SPIKE_CRITICAL_PERCENT,
        warning_threshold_value=ERRORS_SPIKE_WARNING_PERCENT,
        alert_frequency=ERRORS_SPIKE_FREQUENCY_MINUTES,
        auto_threshold_time_window=HISTORY_MINUTES,
    ),
    MonitorSpec(
        kind=DAILY_TOKENS,
        name="Daily tokens over 1,000,000",
        metric_type=MonitorMetricTypeChoices.DAILY_TOKENS_SPENT.value,
        threshold_type=ThresholdCalculationMethodChoices.STATIC.value,
        threshold_operator=ComparisonOperatorChoices.GREATER_THAN.value,
        critical_threshold_value=DAILY_TOKENS_LIMIT,
        warning_threshold_value=None,
        alert_frequency=DAILY_TOKENS_FREQUENCY_MINUTES,
        auto_threshold_time_window=HISTORY_MINUTES,
    ),
)

# The link field that remembers each default.
LINK_FIELD_BY_KIND = {
    ERRORS_SPIKE: "errors_monitor",
    DAILY_TOKENS: "daily_tokens_monitor",
}

# Per link kind: the pair, the messages a deferral reads (``err_dict`` prefix) and the
# log event prefix.
DEFAULTS_BY_LINK_KIND = {
    ObserveLinkKind.DEPLOYMENT: DEFAULT_MONITORS,
    ObserveLinkKind.AGENT: AGENT_DEFAULT_MONITORS,
}
MESSAGE_PREFIX_BY_LINK_KIND = {
    ObserveLinkKind.DEPLOYMENT: "OMEGA_DEPLOYMENT_OBSERVE",
    ObserveLinkKind.AGENT: "OMEGA_AGENT_OBSERVE",
}
LOG_PREFIX_BY_LINK_KIND = {
    ObserveLinkKind.DEPLOYMENT: "omega_deployment_observe",
    ObserveLinkKind.AGENT: "omega_agent_observe",
}
EE_ID_LOG_FIELD_BY_LINK_KIND = {
    ObserveLinkKind.DEPLOYMENT: "ee_deployment_id",
    ObserveLinkKind.AGENT: "ee_agent_id",
}


def default_monitors_for(link_kind: str) -> tuple[MonitorSpec, ...]:
    """The pair a link of ``link_kind`` sets up, in the order the answer lists them."""
    return DEFAULTS_BY_LINK_KIND[link_kind]


def filters_for(link_kind: str) -> dict:
    """The monitor filters a link of ``link_kind`` gives its pair (module docstring)."""
    if link_kind == ObserveLinkKind.DEPLOYMENT:
        return production_without_checks_filters()
    return {}
