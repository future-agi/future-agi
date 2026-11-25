"""The environment a simulated run gives a customer's code agent in the sandbox.

The platform never edits the customer's code, so the only way its traces can say "this
was a simulation" is the standard OpenTelemetry resource variable: every span of an SDK
that reads it carries the mark the hiding readers look for. When the platform
decided the run exports, the same variable names the agent's project (the collector
requires one on every resource) and the export names point the process at our
collector. Keys never ride here: this dictionary is written into the job document,
and the launcher ships credentials as sandbox secrets instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal
from urllib.parse import quote

from tracer.services.simulation_visibility import RESOURCE_MARK_KEY

_MARK_VARIABLE = "OTEL_RESOURCE_ATTRIBUTES"
_MARK_KEY_PREFIX = "fi.simulation."
_MARK_VALUE = "simulation"
_PROJECT_NAME_KEY = "project_name"
_EXPORT_VARIABLE = "FI_SIMULATION_EXPORT"
_TRACES_ENDPOINT_VARIABLE = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
_TRACES_PATH = "/v1/traces"
_TRACEAI_BASE_URL_VARIABLE = "FI_BASE_URL"
# An agent that traces nothing itself is measured by the sandbox: it turns on the model SDKs'
# instrumentors and sends their spans here. No agent reads these names, so an OpenTelemetry
# endpoint the agent set for its own exporters keeps pointing where it did.
_MEASURE_VARIABLE = "FI_SIMULATION_INSTRUMENT"
_MEASURE_VALUE = "traceai"
_MEASURED_TRACES_ENDPOINT_VARIABLE = "FI_SIMULATION_TRACES_ENDPOINT"
# The hosts the agent's model calls go to that the sandbox would not know as model APIs: the
# platform's model gateway and any model base URL the agent declares. A request to one outside
# a measured SDK call is a model call the sandbox did not measure, so its call reads partial.
_MODEL_HOSTS_VARIABLE = "FI_SIMULATION_MODEL_HOSTS"
# The launcher refuses any longer value, and a refused dictionary loses the mark.
_MAX_VALUE_LENGTH = 2048


def _pair(key: str, value: object) -> str:
    # The variable is read as comma-separated key=value pairs and percent-decoded, so an
    # agent name or id carrying "," or "=" (or non-ASCII text) would otherwise break its
    # pair, and the launcher accepts printable ASCII only.
    return f"{key}={quote(str(value), safe='')}"


def customer_code_environment(
    *,
    organization_id: str,
    job_id: str,
    run_id: str,
    run_test_id: str | None,
    test_execution_id: str | None,
    project_name: str | None = None,
    collector_url: str | None = None,
    export: Literal["our_keys"] | None = None,
    instrumentation: str | None = None,
    model_hosts: Iterable[str] = (),
) -> dict[str, str]:
    """Return the variables to set in the customer's process for this run.

    ``organization_id`` is accepted because the launcher passes it, and is never written:
    the run ids already identify the run.
    """
    # The sandbox always exports with the agent's ingest key; no other mode exists.
    if export is not None and export != "our_keys":
        raise ValueError("export must be our_keys")

    # Without a run there is nothing to mark, so nothing is written at all.
    if not run_id:
        return {}

    # An export is all or nothing: without both a project name and a collector URL the
    # run is marked only, exactly as a run that does not export.
    if export is None or not project_name or not collector_url:
        project_pairs: list[str] = []
        export_entries: dict[str, str] = {}
    else:
        # The project pair leads so the sandbox's merge keeps the order it documents:
        # the customer's own pairs, then our project, then the mark.
        project_pairs = [_pair(_PROJECT_NAME_KEY, project_name)]
        traces_endpoint = f"{collector_url}{_TRACES_PATH}"
        if instrumentation == "none":
            export_entries = {
                _EXPORT_VARIABLE: export,
                _MEASURE_VARIABLE: _MEASURE_VALUE,
                _MEASURED_TRACES_ENDPOINT_VARIABLE: traces_endpoint,
            }
            hosts = sorted(
                {host.strip().lower() for host in model_hosts if host.strip()}
            )
            if hosts:
                export_entries[_MODEL_HOSTS_VARIABLE] = ",".join(hosts)
        else:
            export_entries = {
                _EXPORT_VARIABLE: export,
                # The traces-only name, so a metrics or logs exporter never follows it.
                _TRACES_ENDPOINT_VARIABLE: traces_endpoint,
            }
        # traceAI reads its own base URL, not the OpenTelemetry endpoint.
        if instrumentation == "traceai":
            export_entries[_TRACEAI_BASE_URL_VARIABLE] = collector_url

    pairs = [*project_pairs, _pair(RESOURCE_MARK_KEY, _MARK_VALUE)]
    for name, value in (
        ("job_id", job_id),
        ("run_id", run_id),
        ("run_test_id", run_test_id),
        ("test_execution_id", test_execution_id),
    ):
        if value is not None:
            pairs.append(_pair(f"{_MARK_KEY_PREFIX}{name}", value))
    environment = {_MARK_VARIABLE: ",".join(pairs), **export_entries}

    for text in environment.values():
        if len(text) > _MAX_VALUE_LENGTH:
            raise ValueError("simulation mark too long")
        if not (text.isascii() and text.isprintable()):
            # Pairs are percent-encoded; the collector URL is written as given, so it
            # is the free-form value that can carry text the launcher refuses.
            raise ValueError("collector_url must be printable ASCII")
    return environment
