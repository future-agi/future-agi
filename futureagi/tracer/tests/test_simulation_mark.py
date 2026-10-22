"""The environment a simulated run gives a customer's code agent.

The function reads only its arguments, so nothing here touches Postgres, ClickHouse or
the network, and no test is marked ``django_db``.
"""

import inspect
import itertools
import re
import uuid
from typing import Literal, get_type_hints

import pytest

from tracer.services.simulation_mark import customer_code_environment
from tracer.services.simulation_visibility import RESOURCE_MARK_KEY

pytestmark = pytest.mark.unit

# The launcher's acceptance rule for this dictionary, held here so a result it would
# refuse (and launch unmarked) fails before release.
LAUNCHER_NAME_RULE = re.compile(r"^(OTEL_|FI_SIMULATION_|FI_BASE_URL$)[A-Z0-9_]*$")
NEVER_WRITTEN = {
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "FI_API_KEY",
    "FI_SECRET_KEY",
    "FI_PROJECT_NAME",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
}

ORGANIZATION = "org-7f3a9c"
JOB = "job-1"
RUN = "run-2"
RUN_TEST = "rt-3"
EXECUTION = "te-4"
COLLECTOR = "https://collector.example"
MARK = f"{RESOURCE_MARK_KEY}=simulation"
IDS = (
    f"fi.simulation.job_id={JOB},fi.simulation.run_id={RUN},"
    f"fi.simulation.run_test_id={RUN_TEST},fi.simulation.test_execution_id={EXECUTION}"
)
MARK_ALONE = {"OTEL_RESOURCE_ATTRIBUTES": f"{MARK},{IDS}"}


def _environment(**overrides):
    arguments = {
        "organization_id": ORGANIZATION,
        "job_id": JOB,
        "run_id": RUN,
        "run_test_id": RUN_TEST,
        "test_execution_id": EXECUTION,
    }
    arguments.update(overrides)
    return customer_code_environment(**arguments)


def _attribute_keys(result):
    return [
        pair.split("=", 1)[0] for pair in result["OTEL_RESOURCE_ATTRIBUTES"].split(",")
    ]


def _assert_launcher_accepts(result):
    assert isinstance(result, dict)
    assert len(result) <= 4
    for name, value in result.items():
        assert LAUNCHER_NAME_RULE.match(name), name
        assert name not in NEVER_WRITTEN, name
        assert isinstance(value, str), name
        assert len(value) <= 2048, name
        assert value.isascii() and value.isprintable(), value


def _attributes_length(**overrides):
    return len(_environment(**overrides)["OTEL_RESOURCE_ATTRIBUTES"])


def test_mark_is_bounded_and_total():
    ids = (None, "plain", uuid.UUID("3f2b8c1e-9d4a-4e6b-8a1f-0c7d5e2b9a64"), "Zoë, a=b")
    for job_id, run_test_id, test_execution_id in itertools.product(ids, repeat=3):
        for project_name, collector_url, export, instrumentation in itertools.product(
            (None, "Support Bot, prod=1 ü"),
            (None, COLLECTOR),
            (None, "our_keys"),
            (None, "traceai", "opentelemetry"),
        ):
            result = _environment(
                job_id=job_id,
                run_test_id=run_test_id,
                test_execution_id=test_execution_id,
                project_name=project_name,
                collector_url=collector_url,
                export=export,
                instrumentation=instrumentation,
            )
            _assert_launcher_accepts(result)
            attributes = result["OTEL_RESOURCE_ATTRIBUTES"]
            assert MARK in attributes.split(",")
            assert "None" not in attributes
            # The sandbox's hook carries only these pairs into every resource.
            assert all(
                key == "project_name" or key.startswith("fi.simulation.")
                for key in _attribute_keys(result)
            )

    # At the bound the value passes; one character over raises, on the mark alone and
    # with the project pair.
    long_id = "a" * (2048 - _attributes_length(run_test_id="a") + 1)
    assert _attributes_length(run_test_id=long_id) == 2048
    with pytest.raises(ValueError, match=r"^simulation mark too long$"):
        _environment(run_test_id=long_id + "a")

    exporting = {
        "collector_url": COLLECTOR,
        "export": "our_keys",
        "instrumentation": "traceai",
    }
    long_name = "a" * (2048 - _attributes_length(project_name="a", **exporting) + 1)
    assert _attributes_length(project_name=long_name, **exporting) == 2048
    _assert_launcher_accepts(_environment(project_name=long_name, **exporting))
    with pytest.raises(ValueError, match=r"^simulation mark too long$"):
        _environment(project_name=long_name + "a", **exporting)

    # The bound counts the text as written, after encoding: 700 commas are 2100.
    with pytest.raises(ValueError, match=r"^simulation mark too long$"):
        _environment(project_name="," * 700, **exporting)

    # A collector URL is written as given, so it is held to the same rule.
    our_keys = {"project_name": "p", "export": "our_keys", "instrumentation": "traceai"}
    with pytest.raises(ValueError, match=r"^simulation mark too long$"):
        _environment(collector_url=COLLECTOR + "/" + "a" * 2048, **our_keys)
    for unprintable in ("https://collé.example", "https://c.example/\n"):
        with pytest.raises(
            ValueError, match=r"^collector_url must be printable ASCII$"
        ):
            _environment(collector_url=unprintable, **our_keys)


def test_exact_value_and_order():
    results = []

    def environment(**overrides):
        result = _environment(**overrides)
        results.append(result)
        return result

    # No export: the mark alone, whatever project and collector are passed.
    assert environment() == MARK_ALONE
    assert (
        environment(
            project_name="Support Bot",
            collector_url=COLLECTOR,
            instrumentation="traceai",
        )
        == MARK_ALONE
    )

    # An export without a project or a collector stays the mark alone.
    for export in ("our_keys",):
        for project_name, collector_url in (
            (None, COLLECTOR),
            ("", COLLECTOR),
            ("Support Bot", None),
            ("Support Bot", ""),
        ):
            assert (
                environment(
                    project_name=project_name,
                    collector_url=collector_url,
                    export=export,
                    instrumentation="traceai",
                )
                == MARK_ALONE
            )

    with_project = f"project_name=Support%20Bot,{MARK},{IDS}"
    assert environment(
        project_name="Support Bot",
        collector_url=COLLECTOR,
        export="our_keys",
        instrumentation="traceai",
    ) == {
        "OTEL_RESOURCE_ATTRIBUTES": with_project,
        "FI_SIMULATION_EXPORT": "our_keys",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://collector.example/v1/traces",
        "FI_BASE_URL": "https://collector.example",
    }
    assert environment(
        project_name="Support Bot",
        collector_url=COLLECTOR,
        export="our_keys",
        instrumentation="opentelemetry",
    ) == {
        "OTEL_RESOURCE_ATTRIBUTES": with_project,
        "FI_SIMULATION_EXPORT": "our_keys",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://collector.example/v1/traces",
    }
    # The sandbox always exports with the agent's ingest key: no other mode exists.
    for export in ("customer_keys", ""):
        with pytest.raises(ValueError, match=r"^export must be our_keys$"):
            _environment(
                project_name="Support Bot",
                collector_url=COLLECTOR,
                export=export,
                instrumentation="traceai",
            )

    # ",", "=", "/" and non-ASCII text are percent-encoded, in the name and in an id.
    assert environment(
        project_name="Café, a=b/c",
        run_test_id="rt,1=x",
        collector_url=COLLECTOR,
        export="our_keys",
        instrumentation="traceai",
    )["OTEL_RESOURCE_ATTRIBUTES"] == (
        f"project_name=Caf%C3%A9%2C%20a%3Db%2Fc,{MARK},"
        f"fi.simulation.job_id={JOB},fi.simulation.run_id={RUN},"
        "fi.simulation.run_test_id=rt%2C1%3Dx,"
        f"fi.simulation.test_execution_id={EXECUTION}"
    )

    for result in results:
        _assert_launcher_accepts(result)
        keys = _attribute_keys(result)
        if "FI_SIMULATION_EXPORT" in result:
            assert keys[0] == "project_name"
        else:
            assert "project_name" not in keys
        for value in result.values():
            assert ORGANIZATION not in value
            assert "X-Api-Key" not in value
            assert "X-Secret-Key" not in value


def test_none_ids_left_out_and_empty_run_is_empty():
    everything = {
        "project_name": "Support Bot",
        "collector_url": COLLECTOR,
        "export": "our_keys",
        "instrumentation": "traceai",
    }
    for run_id in ("", None):
        assert _environment(run_id=run_id) == {}
        assert _environment(run_id=run_id, **everything) == {}

    assert _environment(run_test_id=None, test_execution_id=None) == {
        "OTEL_RESOURCE_ATTRIBUTES": f"{MARK},fi.simulation.job_id={JOB},"
        f"fi.simulation.run_id={RUN}"
    }
    # An empty id is still an id the caller gave: only None leaves its pair out.
    assert _environment(run_test_id="")["OTEL_RESOURCE_ATTRIBUTES"] == (
        f"{MARK},fi.simulation.job_id={JOB},fi.simulation.run_id={RUN},"
        f"fi.simulation.run_test_id=,fi.simulation.test_execution_id={EXECUTION}"
    )
    assert _environment(job_id=None, test_execution_id=None, **everything)[
        "OTEL_RESOURCE_ATTRIBUTES"
    ] == (
        f"project_name=Support%20Bot,{MARK},fi.simulation.run_id={RUN},"
        f"fi.simulation.run_test_id={RUN_TEST}"
    )

    names = ("job_id", "run_test_id", "test_execution_id")
    for given in itertools.product((False, True), repeat=len(names)):
        ids = {
            name: (f"v-{name}" if on else None)
            for name, on in zip(names, given, strict=True)
        }
        result = _environment(**ids)
        keys = _attribute_keys(result)
        for name, on in zip(names, given, strict=True):
            assert keys.count(f"fi.simulation.{name}") == int(on)
        assert keys.count("fi.simulation.run_id") == 1
        assert "None" not in result["OTEL_RESOURCE_ATTRIBUTES"]

    # An id given as a UUID object is written as its hyphenated str() form, never its
    # hex form, and the organization's is not written at all.
    organization, job, run, run_test, execution = (
        uuid.UUID("5e4d3c2b-1a09-4f8e-8d7c-6b5a49382716"),
        uuid.UUID("0b8f8f5e-3c1d-4e2a-9b7c-1d2e3f4a5b6c"),
        uuid.UUID("1c9a0b6f-4d2e-4f3b-8c8d-2e3f4a5b6c7d"),
        uuid.UUID("2da1bc7a-5e3f-4a4c-9d9e-3f4a5b6c7d8e"),
        uuid.UUID("3eb2cd8b-6f4a-4b5d-8eaf-4a5b6c7d8e9f"),
    )
    result = _environment(
        organization_id=organization,
        job_id=job,
        run_id=run,
        run_test_id=run_test,
        test_execution_id=execution,
    )
    assert result == {
        "OTEL_RESOURCE_ATTRIBUTES": f"{MARK},fi.simulation.job_id={job},"
        f"fi.simulation.run_id={run},fi.simulation.run_test_id={run_test},"
        f"fi.simulation.test_execution_id={execution}"
    }
    assert str(organization) not in result["OTEL_RESOURCE_ATTRIBUTES"]


def test_signature_and_written_names_match_the_contract():
    parameters = inspect.signature(customer_code_environment).parameters
    assert list(parameters) == [
        "organization_id",
        "job_id",
        "run_id",
        "run_test_id",
        "test_execution_id",
        "project_name",
        "collector_url",
        "export",
        "instrumentation",
    ]
    assert {parameter.kind for parameter in parameters.values()} == {
        inspect.Parameter.KEYWORD_ONLY
    }
    assert {name: parameter.default for name, parameter in parameters.items()} == {
        "organization_id": inspect.Parameter.empty,
        "job_id": inspect.Parameter.empty,
        "run_id": inspect.Parameter.empty,
        "run_test_id": inspect.Parameter.empty,
        "test_execution_id": inspect.Parameter.empty,
        "project_name": None,
        "collector_url": None,
        "export": None,
        "instrumentation": None,
    }
    assert get_type_hints(customer_code_environment) == {
        "organization_id": str,
        "job_id": str,
        "run_id": str,
        "run_test_id": str | None,
        "test_execution_id": str | None,
        "project_name": str | None,
        "collector_url": str | None,
        "export": Literal["our_keys"] | None,
        "instrumentation": str | None,
        "return": dict[str, str],
    }

    # The names and words the function writes; the mark's key is the marks module's
    # constant, under the prefix the sandbox's hook keeps.
    result = customer_code_environment(
        organization_id="o",
        job_id="j",
        run_id="r",
        run_test_id="t",
        test_execution_id="e",
        project_name="p",
        collector_url="https://c.example",
        export="our_keys",
        instrumentation="traceai",
    )
    assert result == {
        "OTEL_RESOURCE_ATTRIBUTES": f"project_name=p,{RESOURCE_MARK_KEY}=simulation,"
        "fi.simulation.job_id=j,fi.simulation.run_id=r,"
        "fi.simulation.run_test_id=t,fi.simulation.test_execution_id=e",
        "FI_SIMULATION_EXPORT": "our_keys",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://c.example/v1/traces",
        "FI_BASE_URL": "https://c.example",
    }
    assert RESOURCE_MARK_KEY.startswith("fi.simulation.")
    assert _attribute_keys(result)[1] == RESOURCE_MARK_KEY
