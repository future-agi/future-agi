"""An org's cache.semantic is refused (docs-agentcc-711, item 4; cloud proof
j-20261028t0728-c95dee, record 4-semantic-cache).

The gateway reads an org's cache.enabled, default_ttl and backend, never its
cache.semantic: the semantic tier exists only in the gateway's own config
file, for every org at once. On the proof's gateway update-config saved
cache.semantic {enabled: true, ...} with warnings [] and pushed it; an exact
repeat hit the response cache, and near-duplicate prompts never hit.

A new or changed cache.semantic is now refused with 400
semantic_cache_not_supported (param cache.semantic) on every config write
path. A value the active version already holds stays readable, is kept on
saves that leave it alone (a read-back save with its masks included), and
can be removed; activating a version never refuses it.

Pushes go to a recording gateway client through the real push code.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.services.org_config_secrets import encrypt_config_secrets
from agentcc.services.semantic_cache_rules import (
    SemanticCacheNotSupported,
    check_semantic_cache,
)
from agentcc.views import gateway as gateway_views

GATEWAY = "/agentcc/gateways/default"
UPDATE_CONFIG = f"{GATEWAY}/update-config/"
ORG_CONFIGS = "/agentcc/org-configs/"
CODE = "semantic_cache_not_supported"
MASK = "__encrypted__"

# What the proof saved.
SEMANTIC = {
    "enabled": True,
    "backend": "memory",
    "threshold": 0.5,
    "dimensions": 64,
    "max_entries": 100,
}


def _vector_store(key):
    return {
        "enabled": True,
        "backend": "qdrant",
        "threshold": 0.9,
        "qdrant": {"url": "https://qdrant.sem.example", "api_key": key},
    }


@pytest.fixture
def gateway():
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


def _pushes(gateway, org):
    return [
        call.args[1]
        for call in gateway.set_org_config.call_args_list
        if call.args[0] == str(org.id)
    ]


def _active(org):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )


def _seed(org, cache, version=1, is_active=True):
    """A version an earlier release stored, its secrets encrypted as the
    writer stores them."""
    config = AgentccOrgConfig(
        organization=org, version=version, is_active=is_active, cache=cache
    )
    encrypt_config_secrets(config, None)
    config.save()
    return config


def _refused(response):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    body = response.json()
    assert body["code"] == CODE, body
    assert body["param"] == "cache.semantic", body


@pytest.mark.integration
@pytest.mark.api
class TestANewValueIsRefused:
    def test_update_config_refuses_it_and_saves_and_pushes_nothing(
        self, auth_client, organization, gateway
    ):
        _seed(organization, {"enabled": False})

        _refused(
            auth_client.post(
                UPDATE_CONFIG,
                {"cache": {"enabled": True, "default_ttl": 300, "semantic": SEMANTIC}},
                format="json",
            )
        )

        assert _active(organization).version == 1
        assert _pushes(gateway, organization) == []

    def test_the_response_cache_alone_is_saved_and_pushed(
        self, auth_client, organization, gateway
    ):
        _seed(organization, {"enabled": False})

        response = auth_client.post(
            UPDATE_CONFIG,
            {"cache": {"enabled": True, "default_ttl": 300}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert response.json()["result"]["warnings"] == []
        pushed = _pushes(gateway, organization)[-1]["cache"]
        assert pushed["enabled"] is True and pushed["default_ttl"] == 300
        assert "semantic" not in pushed

    def test_an_org_with_no_version_yet_is_refused(
        self, auth_client, organization, gateway
    ):
        _refused(
            auth_client.post(
                UPDATE_CONFIG, {"cache": {"semantic": SEMANTIC}}, format="json"
            )
        )
        assert not AgentccOrgConfig.no_workspace_objects.filter(
            organization=organization
        ).exists()

    def test_post_org_configs_refuses_it(self, auth_client, organization, gateway):
        _seed(organization, {"enabled": False})

        _refused(
            auth_client.post(
                ORG_CONFIGS, {"cache": {"semantic": SEMANTIC}}, format="json"
            )
        )
        assert _active(organization).version == 1


@pytest.mark.integration
@pytest.mark.api
class TestAStoredValue:
    def _stored(self, organization):
        key = f"sem-key-{uuid.uuid4().hex[:8]}"
        _seed(organization, {"enabled": True, "semantic": _vector_store(key)})
        return key

    def test_stays_readable_and_is_kept_and_pushed_on_other_saves(
        self, auth_client, organization, gateway
    ):
        key = self._stored(organization)

        response = auth_client.post(
            UPDATE_CONFIG, {"cache": {"default_ttl": 600}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        pushed = _pushes(gateway, organization)[-1]["cache"]["semantic"]
        assert pushed["qdrant"]["api_key"] == key
        assert pushed["threshold"] == 0.9
        read = auth_client.get(f"{ORG_CONFIGS}active/").json()["result"]
        assert read["cache"]["semantic"]["threshold"] == 0.9
        assert read["cache"]["semantic"]["qdrant"]["api_key"].startswith(MASK)
        assert key not in str(read)

    def test_a_read_back_save_is_kept(self, auth_client, organization, gateway):
        self._stored(organization)
        read = auth_client.get(f"{ORG_CONFIGS}active/").json()["result"]

        response = auth_client.post(
            ORG_CONFIGS, {"cache": {**read["cache"], "default_ttl": 900}}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).version == 2

    def test_the_same_key_typed_again_is_kept(self, auth_client, organization, gateway):
        key = self._stored(organization)

        response = auth_client.post(
            UPDATE_CONFIG,
            {"cache": {"semantic": {"qdrant": {"api_key": key}}}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()

    @pytest.mark.parametrize(
        "change",
        [
            {"threshold": 0.5},
            {"enabled": False},
            {"qdrant": {"api_key": "another-key"}},
        ],
        ids=["threshold", "enabled", "key"],
    )
    def test_a_change_is_refused(self, auth_client, organization, gateway, change):
        self._stored(organization)

        _refused(
            auth_client.post(
                UPDATE_CONFIG, {"cache": {"semantic": change}}, format="json"
            )
        )
        assert _active(organization).version == 1

    @pytest.mark.parametrize("cleared", [None, {}], ids=["null", "empty"])
    def test_it_can_be_removed(self, auth_client, organization, gateway, cleared):
        self._stored(organization)

        response = auth_client.post(
            ORG_CONFIGS,
            {"cache": {"enabled": True, "semantic": cleared}},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert not _active(organization).cache.get("semantic")

    def test_activating_a_version_that_holds_it_is_never_refused(
        self, auth_client, organization, gateway
    ):
        older = _seed(
            organization,
            {"semantic": _vector_store("older-key")},
            version=1,
            is_active=False,
        )
        _seed(organization, {"enabled": True}, version=2)

        response = auth_client.post(f"{ORG_CONFIGS}{older.id}/activate/")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _active(organization).version == 1


class TestTheRule:
    def _cfg(self, cache):
        return AgentccOrgConfig(organization_id=uuid.uuid4(), cache=cache)

    def test_a_legacy_clear_key_and_its_ciphertext_are_the_same_value(self, db):
        legacy = self._cfg({"semantic": _vector_store("legacy-clear")})
        new = self._cfg({"semantic": _vector_store("legacy-clear")})
        encrypt_config_secrets(new, None)
        assert new.cache["semantic"]["qdrant"]["api_key"].startswith("enc::")
        check_semantic_cache(legacy, new)

    def test_a_new_value_without_an_active_version_is_refused(self):
        with pytest.raises(SemanticCacheNotSupported):
            check_semantic_cache(None, self._cfg({"semantic": SEMANTIC}))

    @pytest.mark.parametrize("cleared", [None, {}, ""])
    def test_clearing_is_never_refused(self, cleared):
        check_semantic_cache(
            self._cfg({"semantic": SEMANTIC}), self._cfg({"semantic": cleared})
        )
