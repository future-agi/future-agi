"""IR-CN7-2 (AM-C20 item 2) and AM-C22 (the CW2 preamble): Django's
push-time placement rule is the gateway's. mcp_credential_placement's
check_secret answers every vector in the gateway's shared file
(credtemplate/testdata/placement-vectors.json, which the gateway's
TestPlacementVectors reads too) as credtemplate.Check does, and
check_catalog_secret, with the file's catalog read by
connector_catalog_parts.from_providers, every vector with a catalog_ref as
credtemplate.ForCatalogSecret does: the same refusals, naming the same
field, and no reason that holds the value. If either side drifts, its own
test fails.

pushed_placement_problem is what the push applies: check_catalog_secret for
an org server's secret that states a placement (in, name or parts) or whose
server has a catalog_ref, as the gateway's admin PUT does
(credtemplate.Applies); the unchanged header path for any other; the
header-only rule for a managed tool credential."""

import json
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from agentcc.services import connector_catalog_parts as catalog_parts
from agentcc.services import mcp_credential_placement as placement
from agentcc.services.connector_catalog_parts import Declared

VECTORS = (
    Path(__file__).resolve().parents[3]
    / "agentcc-gateway/internal/mcp/credtemplate/testdata/placement-vectors.json"
)
DJANGO_COPY = Path(__file__).resolve().parents[1] / "connector_catalog" / "catalog.json"


def _doc():
    return json.loads(VECTORS.read_text())


def _vectors():
    return _doc()["vectors"]


FIXTURE_CATALOG = catalog_parts.from_providers(_doc()["catalog"]["providers"])


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["name"])
def test_check_secret_agrees_with_the_gateway(vector):
    if vector.get("catalog_ref"):
        got = placement.check_catalog_secret(
            vector["url"],
            vector["secret"],
            vector.get("reference_header", ""),
            vector["catalog_ref"],
            None if vector.get("no_catalog") else FIXTURE_CATALOG,
        )
    else:
        got = placement.check_secret(
            vector["url"], vector["secret"], vector.get("reference_header", "")
        )
    if vector["ok"]:
        assert got is None, f"refused {got and got[0]}"
        return
    assert got is not None and got[0] == vector["field"], got
    value = vector["secret"].get("value") or ""
    if len(value) > 2:
        assert value not in got[1]


def test_the_vectors_cover_every_placement():
    seen = {v["secret"].get("in") or "" for v in _vectors()}
    assert {"", "header", "query", "url_variable"} <= seen
    assert any(v["secret"].get("parts") and v["ok"] for v in _vectors())
    # The catalog rules (AM-C22): every provider of the fixture is placed by
    # some vector, and each refusal the catalog decides is among them.
    by_catalog = [v for v in _vectors() if v.get("catalog_ref")]
    assert len(FIXTURE_CATALOG) == len(_doc()["catalog"]["providers"]) >= 10
    used = {v["catalog_ref"].split("@")[0] for v in by_catalog}
    providers = _doc()["catalog"]["providers"]
    assert {f"{p['app']}/{p['id']}" for p in providers} <= used
    # AM-C23 (a): with no catalog loaded the pushed parts are still checked
    # first, then the catalog_ref is refused.
    no_catalog = [v for v in by_catalog if v.get("no_catalog")]
    assert len(no_catalog) >= 3 and not any(v["ok"] for v in no_catalog)
    assert {v["field"] for v in no_catalog} >= {"catalog_ref", "type"}
    fields = {v.get("field") for v in by_catalog if not v["ok"]}
    assert {"catalog_ref", "parts", "parts[0].name", "header", "name", "url"} <= fields
    assert sum(v["ok"] for v in by_catalog) >= 10


URL = "https://mcp.example.com/mcp"


@pytest.mark.parametrize(
    "secret, url, catalog_ref, field",
    [
        # A secret pushed as before keeps the gateway's header path,
        # unchecked, so nothing that pushed before CN7 is refused now ...
        ({"type": "api_key", "header": "Host", "value": "v-1"}, URL, None, None),
        (
            {"type": "api_key", "header": "x-k", "value": " v-1"},
            URL + "/{x}",
            None,
            None,
        ),
        # ... unless its server was added from the catalog (the gateway
        # compiles a template for it).
        (
            {"type": "api_key", "header": "Host", "value": "v-1"},
            URL,
            "a/b@1.0.0",
            "header",
        ),
        # A stated placement is checked by the gateway's rule.
        (
            {"type": "api_key", "in": "query", "name": "k", "value": "v-1"},
            URL,
            None,
            None,
        ),
        (
            {"type": "bearer", "in": "query", "name": "k", "value": "v-1"},
            URL,
            None,
            "type",
        ),
        (
            {"type": "api_key", "in": "url_variable", "name": "t", "value": "v-1"},
            URL,
            None,
            "name",
        ),
        (
            {"type": "api_key", "in": "url_variable", "name": "t", "value": "v-1"},
            URL + "/{t}",
            None,
            None,
        ),
        (
            {
                "type": "api_key",
                "in": "header",
                "name": "A",
                "value": "v-1",
                "parts": [{"in": "header", "name": "B", "value": "v-2"}],
            },
            URL,
            None,
            None,
        ),
        (
            {"type": "api_key", "in": "cookie", "name": "s", "value": "v-1"},
            URL,
            None,
            "in",
        ),
        # A managed tool credential still goes in a header only.
        (
            {"type": "api_key", "in": "query", "name": "k", "value": "v-1"},
            None,
            None,
            "in",
        ),
        (
            {
                "type": "api_key",
                "header": "A",
                "value": "v-1",
                "parts": [{"in": "header", "name": "B", "value": "v-2"}],
            },
            None,
            None,
            "parts",
        ),
        ({"type": "api_key", "header": "x-k", "value": "v-1"}, None, None, None),
    ],
    ids=[
        "legacy-header-unchecked",
        "legacy-value-and-url-unchecked",
        "legacy-header-with-catalog-ref",
        "query",
        "bearer-in-query",
        "url-variable-not-in-url",
        "url-variable",
        "parts",
        "unknown-in",
        "managed-query",
        "managed-parts",
        "managed-header",
    ],
)
def test_pushed_placement_problem(secret, url, catalog_ref, field):
    got = placement.pushed_placement_problem(secret, url, None, catalog_ref)
    assert (got[0] if got else None) == field
    if got:
        assert "v-1" not in got[1] and "v-2" not in got[1]


def test_pushed_placement_problem_places_a_catalog_ref_by_the_catalog():
    """A secret pushed as before is checked once its server has a
    catalog_ref (credtemplate.Applies), by the parts the provider declares:
    the close key pushes, a bearer token for close does not, and with no
    catalog the catalog_ref itself is refused."""
    url = "https://mcp.example.com/mcp"
    key = {"type": "api_key", "header": "Close-API-Key", "value": "v-1"}
    bearer = {"type": "bearer", "header": "Authorization", "value": "v-1"}
    ref = "close/api-key@1.3.0"
    assert (
        placement.pushed_placement_problem(key, url, None, ref, FIXTURE_CATALOG) is None
    )
    got = placement.pushed_placement_problem(bearer, url, None, ref, FIXTURE_CATALOG)
    assert got[0] == "parts" and "v-1" not in got[1]
    assert (
        placement.pushed_placement_problem(bearer, url, None, None, FIXTURE_CATALOG)
        is None
    )
    assert placement.pushed_placement_problem(key, url, None, ref)[0] == "catalog_ref"
    # The reference header places a key pushed with no header of its own.
    bare = {"type": "api_key", "value": "v-1"}
    assert (
        placement.pushed_placement_problem(
            bare, url, "Close-API-Key", ref, FIXTURE_CATALOG
        )
        is None
    )


def test_reserved_headers_are_the_gateways():
    """The names the gateway's credtemplate.ReservedHeader refuses (beyond
    Django's pre-CW1 list): the vault, the push and the gateway refuse the
    same headers (TestReservedHeadersCoverEveryList checks this list against
    the gateway's from its side)."""
    for name in (
        "Accept-Encoding",
        "Proxy-Authorization",
        "TE",
        "Traceparent",
        "Upgrade",
        "Mcp-Anything",
        "X-Agentcc-Org",
        "X-Omega-Run",
        "Mcp-Session-Id",
        "Host",
    ):
        assert placement.reserved_header(name), name
    for name in ("Authorization", "X-Api-Key", "Close-API-Key", "DD-API-KEY"):
        assert not placement.reserved_header(name), name


# --- The catalog the push places a catalog_ref server's key by (AM-C22) -------


def test_the_django_copy_is_read_as_the_gateway_reads_its_catalog():
    """CN6's Django copy (equal to the bundle the gateway embeds, by
    agentcc-catalog check) gives close/api-key's declared parts, in order:
    the key, and Close-Scope with its constant value, which the gateway adds
    (TestEmbeddedCloseKeyReachesTheUpstreamWithItsScope)."""
    parts = catalog_parts.load(DJANGO_COPY)
    assert parts is not None
    version, declared = parts.credential_parts("close", "api-key")
    assert version == "1.0.0"
    assert declared == (
        Declared("header", "Close-API-Key", True),
        Declared("header", "Close-Scope", False, "mcp.read"),
    )
    assert parts.credential_parts("linear", "api-key")[1] == (
        Declared("header", "Authorization", True, prefix="Bearer "),
    )
    # The version the record declares: CN24pre-b (6557b51930) bumped
    # apps/github/providers/remote.yaml 1.0.0 -> 1.0.1 when it added its
    # x-omega.publicClient fact; IR-CN24pb-1 (3ee351fe88) rebuilt this copy.
    assert parts.credential_parts("github", "remote") == ("1.0.1", ())
    assert parts.credential_parts("close", "nope") is None
    raw = json.loads(DJANGO_COPY.read_text())
    assert len(parts) == len(raw["providers"])


def test_no_catalog_refuses_every_catalog_ref(tmp_path):
    """A copy that cannot be read is no catalog: every catalog_ref is
    refused, as a gateway with no catalog refuses it, never pushed; a server
    with no catalog_ref is unchanged."""
    secret = {"type": "api_key", "header": "Close-API-Key", "value": "v-1"}
    url = "https://mcp.example.com/mcp"
    with capture_logs() as logs:
        missing = catalog_parts.load(tmp_path / "missing.json")
    assert missing is None
    assert logs[0]["event"] == "connector_catalog_parts_unavailable"
    (tmp_path / "not-a-bundle.json").write_text('{"schema": "other"}')
    assert catalog_parts.load(tmp_path / "not-a-bundle.json") is None
    (tmp_path / "not-json.json").write_text("{")
    assert catalog_parts.load(tmp_path / "not-json.json") is None
    got = placement.pushed_placement_problem(
        secret, url, None, "close/api-key@1.0.0", None
    )
    assert got[0] == "catalog_ref" and "v-1" not in got[1]
    assert placement.pushed_placement_problem(secret, url, None, None, None) is None


def test_a_provider_record_that_cannot_be_read_is_left_out():
    """An unreadable or repeated provider is one the catalog does not have,
    so its catalog_ref is refused; the rest of the catalog stands."""
    good = {
        "app": "close",
        "id": "api-key",
        "version": "1.0.0",
        "auth": {"credentialParts": [{"in": "header", "name": "K", "secret": True}]},
    }
    unreadable = [
        {**good, "id": "no-auth", "auth": None},
        {**good, "id": "bad-parts", "auth": {"credentialParts": {}}},
        {
            **good,
            "id": "bad-secret",
            "auth": {"credentialParts": [{"in": "header", "name": "K"}]},
        },
        {
            **good,
            "id": "bad-value",
            "auth": {
                "credentialParts": [
                    {"in": "header", "name": "K", "secret": False, "value": 1}
                ]
            },
        },
        {**good, "id": "no-version", "version": None},
        "not-a-record",
    ]
    repeated = {**good, "app": "twice"}
    with capture_logs() as logs:
        parts = catalog_parts.from_providers([good, *unreadable, repeated, repeated])
    assert len(parts) == 1
    assert parts.credential_parts("close", "api-key")[0] == "1.0.0"
    for record in unreadable[:-1]:
        assert parts.credential_parts(record["app"], record["id"]) is None
    assert parts.credential_parts("twice", "api-key") is None
    events = [entry["event"] for entry in logs]
    assert events.count("connector_catalog_parts_record_unreadable") == 6
    assert events.count("connector_catalog_parts_provider_repeated") == 1
    assert catalog_parts.parse({"schema": catalog_parts.BUNDLE_SCHEMA}) is None
