"""Deployed request metadata through the configured structured renderer.

Each scenario is observed in what the configured sinks emit (checkout_sinks;
ruling 681 addendum 5), not in the in-memory record.
"""

import io
import logging
from urllib.parse import urlencode

import httpx
import pytest
import structlog
from django.http import QueryDict
from django.test import Client, override_settings

from tfc.logging.config import configure_structlog, get_logging_config
from tfc.settings.settings import MIDDLEWARE as DEPLOYED
from tfc.tests.checkout_sinks import configured_sinks

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", ["httpx-request", "query-dict"])
@pytest.mark.parametrize("checkout", [True, False])
def test_structlog_to_real_json_renderer(tmp_path, monkeypatch, kind, checkout):
    supplied = "synthetic-structured-carrier-value"
    prefix = "/agentcc/managed-credits/checkout" if checkout else "/ordinary"
    route = prefix + "/" + supplied + "/"
    carrier = (
        httpx.Request("GET", "http://localhost" + route)
        if kind == "httpx-request"
        else QueryDict(
            urlencode([("target", route), ("target", "ordinary-final-value")])
        )
    )
    original = repr(carrier)
    monkeypatch.setenv("ENV_TYPE", "production")
    configure_structlog(cache_logger_on_first_use=False)
    configuration = get_logging_config(str(tmp_path))["formatters"]["structured"]
    output = io.StringIO()
    rendered = logging.StreamHandler(output)
    rendered.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=configuration["processor"],
            foreign_pre_chain=configuration["foreign_pre_chain"],
        )
    )
    with configured_sinks("checkout.structured.renderer.consumer") as (source, sinks):
        source.handlers.insert(1, rendered)
        try:
            structlog.get_logger(source.name).error("late metadata", payload=carrier)
        finally:
            source.removeHandler(rendered)
    assert repr(carrier) == original
    assert sinks.emitted() and output.getvalue()
    if checkout:
        sinks_exposed = sinks.leaks(supplied)
        json_exposed = supplied in output.getvalue()
        assert not (sinks_exposed or json_exposed), (
            "checkout session value reached a sink: "
            f"configured_sinks={sinks_exposed} production_json={json_exposed}"
        )
        assert sinks.structured(), "a production line is not one JSON document"
    else:
        assert supplied in sinks.outputs()["record"], (
            "unrelated metadata must be retained"
        )
        if kind == "httpx-request":
            assert supplied in output.getvalue(), (
                "unrelated rendered request must be retained"
            )


@pytest.mark.django_db
def test_querydict_from_actual_request_keeps_every_value_private():
    supplied = "synthetic-actual-querydict-value"
    route = "/agentcc/managed-credits/checkout/" + supplied
    configure_structlog(cache_logger_on_first_use=False)
    with override_settings(
        MIDDLEWARE=DEPLOYED,
        DEBUG=False,
        APPEND_SLASH=True,
        SECURE_SSL_REDIRECT=False,
        ALLOWED_HOSTS=["testserver"],
    ):
        response = Client().get(
            route, {"redirect": [route + "/", "ordinary-final-value"]}
        )
    assert response.status_code == 301
    query = response.wsgi_request.GET
    assert query.getlist("redirect") == [route + "/", "ordinary-final-value"]
    assert list(query.values()) == ["ordinary-final-value"]
    with configured_sinks("checkout.actual.query.params.consumer") as (source, sinks):
        source.error("late request query metadata", extra={"query_params": query})
    assert query.getlist("redirect") == [route + "/", "ordinary-final-value"]
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert supplied not in sinks.outputs()["record"], (
        "checkout session value reached the full-record sink from a request QueryDict"
    )
    assert sinks.leaks(supplied) == [], "checkout session value reached a sink"
