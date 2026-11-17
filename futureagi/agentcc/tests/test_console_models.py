"""The Console's model list: every model the org can run, and who pays today.

The rows follow the gateway's run path (internal/server/handlers.go
orgRouteFor): a model the org's own usable provider serves is ``byok``
(model map alias, then a "<provider>/<model>" id the provider lists, then any
provider that lists it); otherwise a priced managed-catalog model is
``managed`` while the org's Future AGI credits are on. All gateway and
credential I/O is mocked; every value here is synthetic.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from rest_framework.test import APIRequestFactory, force_authenticate

from agentcc.services import console_models as cm
from agentcc.services.gateway_client import GatewayClientError

ORG = SimpleNamespace(id="00000000-0000-4000-8000-0000000000aa")
ROW_KEYS = {
    "model",
    "name",
    "provider",
    "vendor",
    "billing_mode",
    "payers",
    "priced",
    "input_per_mtok_usd",
    "output_per_mtok_usd",
    "cached_input_per_mtok_usd",
    "context_length",
    "top",
    "top_rank",
}


def managed_model(model_id, vendor, billed_in=1.0, billed_out=2.0, **extra):
    row = {
        "id": model_id,
        "name": extra.pop("name", model_id),
        "provider": extra.pop("provider", "fagi"),
        "vendor": vendor,
        "context_length": extra.pop("context_length", 128000),
        "input_per_mtok": billed_in / 1.05 if billed_in is not None else None,
        "output_per_mtok": billed_out / 1.05 if billed_out is not None else None,
    }
    if billed_in is not None:
        row["billed_input_per_mtok"] = billed_in
    if billed_out is not None:
        row["billed_output_per_mtok"] = billed_out
    row.update(extra)
    return row


def fake_client(
    catalog_models=None, credits=None, credits_error=None, catalog_error=None
):
    client = MagicMock()
    if credits_error is not None:
        client.get_org_credits.side_effect = credits_error
    else:
        client.get_org_credits.return_value = credits or {
            "balance_usd": 12.5,
            "markup_percent": 5.0,
        }
    if catalog_error is not None:
        client.get_catalog.side_effect = catalog_error
    else:
        client.get_catalog.return_value = {
            "markup_percent": 5.0,
            "providers": [],
            "models": catalog_models or [],
        }
    return client


@pytest.fixture(autouse=True)
def _fresh_catalog_cache():
    cache.delete(cm.CATALOG_CACHE_KEY)
    yield
    cache.delete(cm.CATALOG_CACHE_KEY)


def listing(
    providers=None,
    model_map=None,
    credits_on=True,
    client=None,
    providers_error=None,
    unsure=None,
):
    def usable(_org_id):
        return providers or {}, unsure or {}

    with (
        patch.object(cm, "get_gateway_client", return_value=client or fake_client()),
        patch.object(
            cm, "_usable_org_providers", side_effect=providers_error or usable
        ),
        patch.object(cm, "_org_settings", return_value=(credits_on, model_map or {})),
    ):
        return cm.list_console_models(ORG)


def by_model(result):
    return {row["model"]: row for row in result["models"]}


def test_listed_models_of_a_usable_provider_are_byok():
    result = listing(providers={"openai": ["gpt-4o", "o3"]})
    rows = by_model(result)
    assert set(rows) == {"gpt-4o", "o3"}
    for row in rows.values():
        assert set(row) == ROW_KEYS
        assert row["billing_mode"] == "byok"
        assert row["payers"] == ["byok"]
        assert row["provider"] == "openai"
    assert result["sources"]["byok"] == {
        "available": True,
        "providers": ["openai"],
        "error": None,
    }


def test_a_model_two_providers_list_runs_on_the_lowest_provider_id():
    providers = {"zeta": ["m1"], "alpha": ["m1"]}
    assert cm._byok_provider("m1", providers, {}) == "alpha"
    rows = by_model(listing(providers=providers))
    # A custom provider is shown by a fixed label, never by its name.
    assert rows["m1"]["provider"] == "custom"


def test_model_map_aliases_route_but_are_never_listed():
    providers = {"anthropic": ["claude-sonnet-4-5"]}
    model_map = {"house-default": "anthropic", "ghost": "missing-provider"}
    rows = by_model(listing(providers=providers, model_map=model_map))
    # Aliases are not listed: they never pass the push's credential-aware
    # projection. The run path still serves one if typed.
    assert set(rows) == {"claude-sonnet-4-5"}
    assert cm._byok_provider("house-default", providers, model_map) == "anthropic"
    assert cm._byok_provider("ghost", providers, model_map) is None


def test_managed_rows_need_credits_on_and_a_billed_price():
    client = fake_client(
        catalog_models=[
            managed_model("anthropic/claude-opus-4.8", "anthropic", 15.75, 78.75),
            managed_model("vendor/unpriced", "vendor", billed_in=None, billed_out=None),
            managed_model("google/gemini-2.5-pro:batch", "google", 0.6, 4.8),
        ]
    )
    rows = by_model(listing(client=client))
    assert set(rows) == {"anthropic/claude-opus-4.8"}
    row = rows["anthropic/claude-opus-4.8"]
    assert row["billing_mode"] == "managed"
    assert row["payers"] == ["managed"]
    assert row["priced"] is True
    assert row["input_per_mtok_usd"] == 15.75
    assert row["output_per_mtok_usd"] == 78.75
    assert row["vendor"] == "anthropic"
    assert row["context_length"] == 128000


def test_credits_off_lists_no_managed_only_model_and_says_why():
    client = fake_client(catalog_models=[managed_model("x/only-managed", "x")])
    result = listing(providers={"openai": ["gpt-4o"]}, credits_on=False, client=client)
    assert set(by_model(result)) == {"gpt-4o"}
    assert result["sources"]["managed"]["enabled"] is False
    assert result["sources"]["managed"]["available"] is True


def test_a_managed_id_the_org_key_serves_by_prefix_is_byok_with_both_payers():
    client = fake_client(
        catalog_models=[managed_model("openai/o4-mini", "openai", 1.2, 4.8)]
    )
    rows = by_model(listing(providers={"OpenAI": ["o4-mini"]}, client=client))
    # Distinct ids are distinct rows: the prefixed catalog id runs on the
    # org's key too (orgPrefixProvider), and managed could also serve it.
    assert rows["openai/o4-mini"]["billing_mode"] == "byok"
    assert rows["openai/o4-mini"]["provider"] == "custom"
    assert rows["openai/o4-mini"]["payers"] == ["byok", "managed"]
    assert rows["o4-mini"]["billing_mode"] == "byok"
    assert rows["o4-mini"]["payers"] == ["byok"]


def test_the_prefix_provider_wins_over_an_earlier_provider_listing_the_full_id():
    rows = by_model(
        listing(providers={"alpha-private": ["openai/gpt-4o"], "openai": ["gpt-4o"]})
    )
    assert rows["openai/gpt-4o"]["provider"] == "openai"


def test_an_exact_id_in_both_is_one_byok_row_with_both_payers():
    client = fake_client(
        catalog_models=[
            managed_model(
                "same-id", "vendor", 3.0, 6.0, name="Same", context_length=64000
            )
        ]
    )
    rows = by_model(listing(providers={"vendorp": ["same-id"]}, client=client))
    row = rows["same-id"]
    assert row["billing_mode"] == "byok"
    assert row["payers"] == ["byok", "managed"]
    assert row["provider"] == "custom"
    assert row["name"] == "Same"
    assert row["context_length"] == 64000
    # Prices follow billing_mode: byok shows the provider's list price, not
    # the price Future AGI would bill.
    assert row["input_per_mtok_usd"] == pytest.approx(3.0 / 1.05)


def test_top_picks_the_newest_version_per_family_and_comes_first():
    client = fake_client(
        catalog_models=[
            managed_model("anthropic/claude-sonnet-4.5", "anthropic"),
            managed_model("anthropic/claude-sonnet-4.6", "anthropic"),
            managed_model("google/gemini-2.5-pro", "google"),
            managed_model("zz/obscure-model", "zz"),
        ]
    )
    result = listing(
        providers={"anthropic": ["claude-sonnet-4-5-20250929"]}, client=client
    )
    rows = result["models"]
    tops = [r for r in rows if r["top"]]
    assert [r["model"] for r in tops] == [
        "anthropic/claude-sonnet-4.6",
        "google/gemini-2.5-pro",
    ]
    assert [r["top_rank"] for r in tops] == [1, 2]
    assert rows[: len(tops)] == tops
    assert by_model(result)["claude-sonnet-4-5-20250929"]["top"] is False
    assert by_model(result)["zz/obscure-model"]["top_rank"] is None


def test_a_dated_native_id_counts_as_its_version():
    rows = by_model(
        listing(
            providers={"anthropic": ["claude-sonnet-4-5-20250929", "claude-sonnet-4"]}
        )
    )
    assert rows["claude-sonnet-4-5-20250929"]["top"] is True
    assert rows["claude-sonnet-4"]["top"] is False


def test_the_list_is_capped_with_top_rows_kept():
    models = [f"model-{i:03d}" for i in range(250)] + ["gpt-5"]
    result = listing(providers={"openai": models})
    assert len(result["models"]) == cm.MAX_ROWS
    assert result["total"] == 251
    assert result["truncated"] is True
    assert result["models"][0]["model"] == "gpt-5"


def test_no_key_and_no_managed_is_an_empty_list_with_reasons():
    error = GatewayClientError("no managed", 404, "managed_models_disabled")
    result = listing(client=fake_client(credits_error=error))
    assert result["models"] == []
    assert result["total"] == 0
    assert result["sources"]["byok"]["available"] is False
    assert result["sources"]["managed"]["available"] is False
    assert result["sources"]["managed"]["error"] is None


def test_a_managed_read_failure_keeps_the_byok_rows():
    error = GatewayClientError("down", 503)
    result = listing(
        providers={"openai": ["gpt-4o"]}, client=fake_client(credits_error=error)
    )
    assert set(by_model(result)) == {"gpt-4o"}
    managed = result["sources"]["managed"]
    assert managed["available"] is False
    assert managed["error"]["code"] == "gateway_unavailable"
    assert "down" not in managed["error"]["message"]


def test_a_provider_read_failure_makes_the_list_unavailable():
    # A managed row's payer depends on the org's own providers: without them
    # the list cannot say who pays, so it does not guess "managed".
    client = fake_client(catalog_models=[managed_model("x/m", "x")])
    with pytest.raises(cm.ConsoleModelsUnavailable) as raised:
        listing(client=client, providers_error=RuntimeError("boom"))
    assert raised.value.status == 503


def test_an_org_config_read_failure_makes_the_list_unavailable():
    with (
        patch.object(cm, "get_gateway_client", return_value=fake_client()),
        patch.object(cm, "_org_settings", side_effect=RuntimeError("db down")),
        pytest.raises(cm.ConsoleModelsUnavailable) as raised,
    ):
        cm.list_console_models(ORG)
    assert raised.value.status == 503


def test_a_catalog_cache_outage_reads_the_gateway_and_keeps_the_rows():
    client = fake_client(catalog_models=[managed_model("x/m", "x")])
    with (
        patch.object(cm.cache, "get", side_effect=ConnectionError("cache down")),
        patch.object(cm.cache, "set", side_effect=ConnectionError("cache down")),
    ):
        rows = by_model(listing(providers={"openai": ["gpt-4o"]}, client=client))
    assert set(rows) == {"gpt-4o", "x/m"}
    assert rows["x/m"]["billing_mode"] == "managed"


def test_usable_providers_reads_only_enabled_entries_and_model_ids():
    pushed = {
        "openai": {
            "enabled": True,
            "models": ["gpt-4o", "o3"],
            "api_key": "synthetic-not-a-key",
        },
        "off": {"enabled": False, "models": ["m"], "api_key": "synthetic-not-a-key"},
        "nomodels": {"enabled": True, "api_key": "synthetic-not-a-key"},
        "nokey": {"enabled": True, "models": ["k"]},
    }
    with (
        patch("agentcc.services.config_push._assemble_providers", return_value=pushed),
        patch.object(cm, "_unassembled_providers", return_value={}),
    ):
        usable, unsure = cm._usable_org_providers(ORG.id)
    assert usable == {"openai": ["gpt-4o", "o3"], "nomodels": []}
    assert unsure == {}


def test_no_alias_or_custom_provider_name_is_ever_emitted():
    secret = "synthetic-secret-12345"
    providers = {f"proxy-{secret}": ["safe-model"], "openai": ["gpt-4o"]}
    model_map = {f"house-{secret}": "openai", "house-alias": f"proxy-{secret}"}
    result = listing(providers=providers, model_map=model_map)
    rows = by_model(result)
    assert set(rows) == {"safe-model", "gpt-4o"}
    assert rows["safe-model"]["provider"] == "custom"
    assert result["sources"]["byok"]["providers"] == ["custom", "openai"]
    assert secret not in json.dumps(result)


def test_a_model_id_with_a_trailing_newline_is_not_listed():
    client = fake_client(
        catalog_models=[managed_model("x/ok", "x"), managed_model("x/nl\n", "x")]
    )
    rows = by_model(
        listing(providers={"openai": ["gpt-4o", "gpt-4o\n"]}, client=client)
    )
    assert set(rows) == {"gpt-4o", "x/ok"}


def test_a_provider_the_push_left_out_hides_the_managed_rows_it_might_serve():
    client = fake_client(
        catalog_models=[
            managed_model("broken/m1", "x"),
            managed_model("other/m2", "x"),
            managed_model("m3", "x"),
        ]
    )
    rows = by_model(
        listing(
            providers={"openai": ["gpt-4o"]},
            client=client,
            unsure={"broken": ["m1"], "legacy": ["m3"]},
        )
    )
    # broken/m1 (prefix + listed) and m3 (listed) might still run on a key the
    # gateway holds from an earlier push: not listed rather than mislabelled.
    assert "broken/m1" not in rows
    assert "m3" not in rows
    assert rows["other/m2"]["billing_mode"] == "managed"
    assert rows["gpt-4o"]["billing_mode"] == "byok"


def test_the_view_answers_the_list_without_caching():
    factory = APIRequestFactory()
    request = factory.get("/agentcc/console-models/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    request.organization = ORG
    result = {"sources": {}, "models": [], "total": 0, "truncated": False}
    with patch("agentcc.views.console_models.list_console_models", return_value=result):
        from agentcc.views.console_models import ConsoleModelsView

        response = ConsoleModelsView.as_view()(request)
    assert response.status_code == 200
    # No browser session here, so no attestation (test_console_models_attestation).
    assert response.data["result"] == {**result, "attestation": None}
    assert response["Cache-Control"] == "no-store"


def test_the_view_needs_an_organization():
    factory = APIRequestFactory()
    request = factory.get("/agentcc/console-models/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    request.organization = None
    from agentcc.views.console_models import ConsoleModelsView

    assert ConsoleModelsView.as_view()(request).status_code == 400


def test_the_view_maps_both_sources_down_to_a_typed_error():
    factory = APIRequestFactory()
    request = factory.get("/agentcc/console-models/")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    request.organization = ORG
    with patch(
        "agentcc.views.console_models.list_console_models",
        side_effect=cm.ConsoleModelsUnavailable(503),
    ):
        from agentcc.views.console_models import ConsoleModelsView

        response = ConsoleModelsView.as_view()(request)
    assert response.status_code == 503
    assert response["Cache-Control"] == "no-store"
    assert "console_models_unavailable" in str(response.data)


# The run path's payer rule as one table, in the same five cases and order as
# the gateway's console-run table test (gateway-console-v2-redesign): the
# list's billing_mode must equal the x-agentcc-billing-mode the run serves.
AGREEMENT_PROVIDERS = {
    "alpha": ["m-both", "m-case"],
    "OpenAI": ["o4-mini"],
    "zeta": ["m-both"],
    "Zeta": ["m-case"],
}
AGREEMENT_MAP = {"house-alias": "zeta"}


@pytest.mark.parametrize(
    ("case", "model", "expected_provider"),
    [
        ("model_map alias", "house-alias", "zeta"),
        ("provider-prefixed id, case-insensitive prefix", "openai/o4-mini", "OpenAI"),
        ("two providers list it: the lower provider id", "m-both", "alpha"),
        # The gateway compares provider ids as bytes: "Zeta" sorts before "alpha".
        ("byte order: Zeta before alpha", "m-case", "Zeta"),
        ("not the org's key", "x/elsewhere", None),
    ],
)
def test_agreement_table_byok_provider(case, model, expected_provider):
    assert (
        cm._byok_provider(model, AGREEMENT_PROVIDERS, AGREEMENT_MAP)
        == expected_provider
    ), case


@pytest.mark.parametrize(
    ("case", "model", "billing_mode"),
    [
        # The run path serves an alias byok; the list does not list aliases.
        ("model_map alias", "house-alias", None),
        ("two providers list it: the lower provider id", "m-both", "byok"),
        ("credits on, catalogued and priced", "x/managed-priced", "managed"),
        ("unpriced managed model", "x/managed-unpriced", None),
        ("batch-only id", "x/managed-priced:batch", None),
    ],
)
def test_agreement_table_billing_mode(case, model, billing_mode):
    client = fake_client(
        catalog_models=[
            managed_model("x/managed-priced", "x"),
            managed_model("x/managed-unpriced", "x", billed_in=None, billed_out=None),
            managed_model("x/managed-priced:batch", "x"),
        ]
    )
    rows = by_model(
        listing(providers=AGREEMENT_PROVIDERS, model_map=AGREEMENT_MAP, client=client)
    )
    if billing_mode is None:
        assert model not in rows, case
    else:
        assert rows[model]["billing_mode"] == billing_mode, case


def test_agreement_table_unusable_providers_are_omitted():
    pushed = {
        "nokey": {"enabled": True, "models": ["k-model"]},
        "off": {
            "enabled": False,
            "models": ["off-model"],
            "api_key": "synthetic-not-a-key",
        },
    }
    with (
        patch("agentcc.services.config_push._assemble_providers", return_value=pushed),
        patch.object(cm, "_unassembled_providers", return_value={}),
    ):
        assert cm._usable_org_providers(ORG.id) == ({}, {})


def test_every_row_model_meets_the_one_id_pattern():
    client = fake_client(
        catalog_models=[
            managed_model("ok/model-1", "x"),
            managed_model("bad model with spaces", "x"),
            managed_model("https://x.invalid/m", "x"),
        ]
    )
    rows = by_model(
        listing(
            providers={"openai": ["gpt-4o", "has space", "-leading-dash"]},
            client=client,
        )
    )
    assert set(rows) == {"gpt-4o", "ok/model-1"}
    assert all(cm._MODEL_ID.match(m) and "://" not in m for m in rows)


def test_priced_means_both_prices_above_zero():
    client = fake_client(
        catalog_models=[
            managed_model("x/free", "x", 0.0, 0.0),
            managed_model("x/half", "x", 1.0, 0.0),
            managed_model("x/paid", "x", 1.0, 2.0),
            managed_model("listed-free", "x", 0.0, 0.0),
        ]
    )
    rows = by_model(listing(providers={"p": ["listed-free"]}, client=client))
    # A managed row with a zero billed price is not listed; a byok row with a
    # zero list price is shown unpriced, never as free.
    assert "x/free" not in rows
    assert "x/half" not in rows
    assert rows["x/paid"]["priced"] is True
    assert rows["listed-free"]["priced"] is False
    assert rows["listed-free"]["input_per_mtok_usd"] is None


def test_router_ids_are_never_listed():
    client = fake_client(
        catalog_models=[
            managed_model("openrouter/auto", "x"),
            managed_model("vendor/x/AUTO", "x"),
            managed_model("omega-default", "x"),
            managed_model("openai/auto-1", "x"),
        ]
    )
    rows = by_model(
        listing(providers={"p": ["Auto", "omega/auto", "auto-mini"]}, client=client)
    )
    assert set(rows) == {"auto-mini", "openai/auto-1"}


# A provider prefix compares as the gateway's Go strings.EqualFold does: simple
# case folding, one character for one. The list must never predict byok for a
# provider the gateway's prefix match would not pick. Synthetic names only.
@pytest.mark.parametrize(
    ("case", "provider", "catalog_id", "billing_mode"),
    [
        ("ASCII case only", "FireWorks", "fireworks/m1", "byok"),
        ("fi ligature is not two letters", "\ufb01reworks", "fireworks/m1", "managed"),
        ("sharp s never folds to ss", "gro\u00df", "gross/m1", "managed"),
        ("the Kelvin sign folds to k", "\u212aimi", "kimi/m1", "byok"),
        ("long s folds to s", "\u017fambanova", "sambanova/m1", "byok"),
        ("dotless i does not fold to i", "\u0131nfra", "infra/m1", "managed"),
        ("dotted capital I does not fold to i", "\u0130nfra", "infra/m1", "managed"),
    ],
)
def test_provider_prefixes_fold_like_go_equal_fold(
    case, provider, catalog_id, billing_mode
):
    client = fake_client(catalog_models=[managed_model(catalog_id, "x", 1.0, 2.0)])
    rows = by_model(listing(providers={provider: ["m1"]}, client=client))
    assert rows[catalog_id]["billing_mode"] == billing_mode, case
    payers = ["byok", "managed"] if billing_mode == "byok" else ["managed"]
    assert rows[catalog_id]["payers"] == payers, case


def test_catalog_details_need_the_exact_model_part():
    client = fake_client(
        catalog_models=[
            managed_model("openai/model-a", "openai", 1.0, 2.0, name="Lower A"),
            managed_model("openai/gpt-x", "openai", 3.0, 6.0, name="GPT X"),
        ]
    )
    rows = by_model(
        listing(
            providers={"OpenAI": ["Model-A", "gpt-x"]}, credits_on=False, client=client
        )
    )
    # The provider part folds; the model part after it is exact, so another
    # model's name and price are never shown.
    assert rows["Model-A"]["name"] == "Model-A"
    assert rows["Model-A"]["priced"] is False
    assert rows["Model-A"]["input_per_mtok_usd"] is None
    assert rows["gpt-x"]["name"] == "GPT X"
    assert rows["gpt-x"]["priced"] is True


# The same seven rows as the gateway's org_route_prefix_fold_test.go
# (TestOrgPrefixProviderFoldsLikeEqualFold), as prefix, provider, match.
GO_FOLD_ROWS = [
    ("fireworks", "FireWorks", True),
    ("fireworks", "\ufb01reworks", False),
    ("gross", "gro\u00df", False),
    ("kimi", "\u212aimi", True),
    ("sambanova", "\u017fambanova", True),
    ("infra", "\u0131nfra", False),
    ("infra", "\u0130nfra", False),
]


@pytest.mark.parametrize(("prefix", "provider", "match"), GO_FOLD_ROWS)
def test_equal_fold_matches_go_in_both_argument_orders(prefix, provider, match):
    assert cm._equal_fold(prefix, provider) is match
    assert cm._equal_fold(provider, prefix) is match


def test_catalog_details_match_a_non_ascii_catalog_prefix_that_folds():
    client = fake_client(
        catalog_models=[
            managed_model("\u212aimi/m1", "moonshot", 1.0, 2.0, name="Kimi M1")
        ]
    )
    rows = by_model(
        listing(providers={"kimi": ["m1"]}, credits_on=False, client=client)
    )
    # The catalog's provider part folds to the provider (Kelvin sign = k); the
    # catalog id itself is never listed (not the one id pattern).
    assert rows["m1"]["name"] == "Kimi M1"
    assert rows["m1"]["priced"] is True
    assert "\u212aimi/m1" not in rows


def test_a_listed_id_the_model_map_sends_to_another_model_is_not_listed():
    providers = {"a": ["m1"], "b": ["m2"]}
    rows = by_model(listing(providers=providers, model_map={"m1": "b"}))
    # orgMappedRoute sends "m1" to b's only model, m2: a different model,
    # which a Console run refuses. m2 itself is still listed.
    assert "m1" not in rows
    assert rows["m2"]["billing_mode"] == "byok"


def test_a_map_entry_that_only_picks_the_provider_keeps_the_row():
    providers = {"a": ["m1"], "b": ["m1", "m3"]}
    rows = by_model(listing(providers=providers, model_map={"m1": "b"}))
    assert rows["m1"]["billing_mode"] == "byok"
    assert cm._byok_provider("m1", providers, {"m1": "b"}) == "b"
    single = {"a": ["m1"], "b": ["m1"]}
    assert "m1" in by_model(listing(providers=single, model_map={"m1": "b"}))


def test_a_catalog_id_the_model_map_sends_to_another_model_is_not_listed():
    client = fake_client(catalog_models=[managed_model("x/cat", "x")])
    rows = by_model(
        listing(providers={"b": ["m2"]}, model_map={"x/cat": "b"}, client=client)
    )
    assert "x/cat" not in rows
    assert "m2" in rows
