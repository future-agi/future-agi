"""The access-cache namespace belongs to one fail-closed storage boundary."""

import ast
import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


class Cache:
    def __init__(self):
        self.values = {}
        self.writes = []
        self.deleted = []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, timeout=None):
        self.values[key] = dict(value)
        self.writes.append((key, dict(value), timeout))

    def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)

    def delete_many(self, keys):
        for key in keys:
            self.delete(key)


def boundary():
    return importlib.import_module("accounts.services.auth_cache")


def digest():
    return hashlib.sha256(uuid4().bytes).hexdigest()


def key(token_id):
    return f"access_token_{token_id}"


def install_totp_seams(monkeypatch, cache, fresh_user, access_ids):
    service = importlib.import_module("accounts.services.totp_service")
    users, tokens = MagicMock(), MagicMock()
    if fresh_user is None:
        users.select_related.return_value.get.side_effect = service.User.DoesNotExist
    else:
        users.select_related.return_value.get.return_value = fresh_user
    tokens.filter.return_value.filter.side_effect = lambda **kw: (
        [SimpleNamespace(id=pk) for pk in access_ids]
        if kw["auth_type"] == service.AuthTokenType.ACCESS.value else []
    )
    monkeypatch.setattr(service.User, "objects", users)
    monkeypatch.setattr(service.AuthToken, "objects", tokens)
    monkeypatch.setattr(service, "cache", cache)
    return service


def test_totp_refresh_evicts_legacy_secondary_session(monkeypatch):
    backend = Cache()
    current, secondary, missing = uuid4(), uuid4(), uuid4()
    fresh_user = SimpleNamespace(pk=uuid4())
    raw_bearer = uuid4().hex
    expected_digest = digest()
    backend.values[key(current)] = {
        "token_digest": expected_digest, "user": object(), "non_saml_v1": True,
    }
    backend.values[key(secondary)] = {"token": raw_bearer, "user": object()}
    service = install_totp_seams(monkeypatch, backend, fresh_user, [current, secondary, missing])
    service._refresh_user_in_cached_tokens(fresh_user.pk)
    assert key(secondary) not in backend.values, "legacy secondary session must be evicted"
    assert key(missing) not in backend.values, "refresh must not create a missing entry"
    assert len(backend.writes) == 1
    assert backend.writes[0][2] == service.AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60
    assert backend.values[key(current)] == {
        "token_digest": expected_digest, "user": fresh_user, "non_saml_v1": True,
    }
    assert all(raw_bearer not in entry.values() for entry in backend.values.values()), "raw bearer retained"


@pytest.mark.parametrize("marker", [None, True, False], ids=["absent", "classified", "unclassified"])
def test_totp_digest_refresh_preserves_marker_and_ttl(monkeypatch, marker):
    backend, token_id = Cache(), uuid4()
    fresh_user = SimpleNamespace(pk=uuid4())
    expected = {"token_digest": digest(), "user": fresh_user}
    if marker is not None:
        expected["non_saml_v1"] = marker
    backend.values[key(token_id)] = dict(expected, user=object())
    service = install_totp_seams(monkeypatch, backend, fresh_user, [token_id])
    service._refresh_user_in_cached_tokens(fresh_user.pk)
    assert backend.values[key(token_id)] == expected
    assert backend.writes[0][2] == service.AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60


def test_totp_missing_user_revokes_both_cache_shapes(monkeypatch):
    backend = Cache()
    ids = [uuid4(), uuid4()]
    backend.values[key(ids[0])] = {"token": uuid4().hex, "user": object()}
    backend.values[key(ids[1])] = {"token_digest": digest(), "user": object(), "non_saml_v1": True}
    service = install_totp_seams(monkeypatch, backend, None, ids)
    service._refresh_user_in_cached_tokens(uuid4())
    assert not backend.values
    assert set(backend.deleted) == {key(pk) for pk in ids}
    assert not backend.writes


@pytest.mark.parametrize("kind", ["legacy", "extra", "missing-digest", "missing-user", "bad-digest", "bad-marker", "scalar", "empty"])
def test_boundary_read_evicts_untrusted_shape(kind):
    backend, token_id = Cache(), uuid4()
    payload = {"token_digest": digest(), "user": object()}
    if kind == "legacy":
        payload = {"token": uuid4().hex, "user": object()}
    elif kind == "extra":
        payload["unexpected"] = uuid4().hex
    elif kind == "missing-digest":
        payload.pop("token_digest")
    elif kind == "missing-user":
        payload.pop("user")
    elif kind == "bad-digest":
        payload["token_digest"] = uuid4().hex
    elif kind == "bad-marker":
        payload["non_saml_v1"] = 1
    elif kind == "scalar":
        payload = uuid4().hex
    else:
        payload = {}
    backend.values[key(token_id)] = payload
    assert boundary().get_cached_access_token(token_id, cache_backend=backend) is None
    assert key(token_id) not in backend.values
    assert backend.deleted == [key(token_id)]
    assert not backend.writes


def test_boundary_refresh_drops_contaminated_payload_without_rewrite():
    backend, token_id = Cache(), uuid4()
    backend.values[key(token_id)] = {
        "token_digest": digest(), "user": object(), "non_saml_v1": True,
        "extra": {"token": uuid4().hex},
    }
    boundary().refresh_cached_access_user(token_id, object(), timeout=31, cache_backend=backend)
    assert key(token_id) not in backend.values
    assert not backend.writes


def test_boundary_write_read_and_revoke_use_same_namespace():
    backend, token_id, user = Cache(), uuid4(), object()
    bearer = uuid4().hex
    module = boundary()
    module.cache_access_token(token_id, bearer, user, timeout=31, cache_backend=backend)
    payload = module.get_cached_access_token(token_id, cache_backend=backend)
    assert set(payload) == {"token_digest", "user"}
    assert payload["user"] is user
    assert module.matches(payload, bearer)
    assert not any(value == bearer for value in payload.values()), "raw bearer stored"
    assert backend.writes[0][2] == 31
    module.revoke_cached_access_token(token_id, cache_backend=backend)
    assert not backend.values
    module.revoke_cached_access_tokens([token_id], cache_backend=backend)
    assert not backend.values


def test_access_cache_namespace_is_reserved_for_boundary():
    module = boundary()
    assert module.is_access_token_cache_key(key(uuid4()))
    assert not module.is_access_token_cache_key(f"refresh_token_{uuid4()}")
    assert not module.is_access_token_cache_key(None)


def test_generic_cache_endpoint_cannot_write_reserved_namespace(monkeypatch):
    from django.test import override_settings
    from rest_framework.test import APIRequestFactory, force_authenticate
    from accounts.views import user as view

    backend = Cache()
    proof, raw = uuid4().hex, uuid4().hex
    monkeypatch.setattr(view, "cache", backend)
    request = APIRequestFactory().post("/", {
        "access_token_id": proof, "key": key(uuid4()), "value": {"token": raw},
    }, format="json")
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    with override_settings(SECRET_KEY=proof):
        response = view.manage_redis_key(request)
    assert response.status_code == 400
    assert not backend.writes


def test_only_boundary_constructs_access_cache_prefix():
    root = Path(__file__).resolve().parents[2]
    permitted = root / "accounts/services/auth_cache.py"
    violations = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if path == permitted or any(part.startswith(".") for part in relative.parts):
            continue
        if "tests" in relative.parts or path.name.startswith("test_") or path.name == "conftest.py":
            continue
        if path.name == "settings.py" or "settings" in relative.parts:
            continue
        if path.name.startswith((".env", "values", "docker-compose")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            # The similarly named request field is not a cache key. All actual
            # key-prefix literals, f-strings, format strings and hard-coded keys
            # belong to the boundary, including revocation and read-only paths.
            if node.value.startswith("access_token_") and node.value != "access_token_id":
                violations.append((str(relative), node.lineno))
    assert not violations, f"access-cache construction outside boundary: {violations}"
