"""Bundled trust-root behavior of the license keyring.

The bundled keys are the sole trust root: once ANY key is bundled,
env/settings keys are ignored entirely — they can neither add a rotation
kid nor replace a bundled one. Otherwise a deployment could validate a
self-signed license by pointing EE_LICENSE_PUBLIC_KEY (or adding a fresh
kid via EE_LICENSE_PUBLIC_KEYS) at its own keypair. Env keys are honored
only while nothing is bundled (the pre-GA / development escape hatch).
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import override_settings
from ee.licensing import keyring
from ee.licensing.keyring import PublicKeyEntry, load_keyring_from_settings

_BUNDLED = PublicKeyEntry(
    kid="prod-2026",
    algorithm="RS256",
    public_key="-----BEGIN PUBLIC KEY-----\nbundled\n-----END PUBLIC KEY-----",
)


def _reload_real_keyring():
    load_keyring_from_settings()


class TestBundledTrustRoot:
    def teardown_method(self):
        _reload_real_keyring()

    def test_env_key_ignored_when_bundled_present(self):
        # An env public key (claims the "default" kid) must NOT be added to
        # the ring while a bundled trust root exists — otherwise a deployment
        # could sign its own license against that env key and validate it.
        with patch.object(keyring, "_BUNDLED_KEYS", (_BUNDLED,)):
            with override_settings(
                EE_LICENSE_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\nrotation\n-----END PUBLIC KEY-----",
                EE_LICENSE_PUBLIC_KEYS="",
            ):
                load_keyring_from_settings()

            assert keyring.get_key("prod-2026") == _BUNDLED
            assert keyring.get_key("default") is None
            # Ring is exactly the bundled set — nothing the env introduced.
            assert keyring.has_any_keys()

    def test_fresh_env_kid_not_added_when_bundled_present(self):
        # The real attack: an operator adds a brand-new kid (no collision
        # with any bundled kid) via EE_LICENSE_PUBLIC_KEYS. It must be
        # ignored, so the validator can never resolve it.
        attacker_keys = (
            '[{"kid": "attacker-kid", "algorithm": "RS256",'
            ' "public_key": "-----BEGIN PUBLIC KEY-----\\nattacker\\n-----END PUBLIC KEY-----"}]'
        )
        with patch.object(keyring, "_BUNDLED_KEYS", (_BUNDLED,)):
            with override_settings(
                EE_LICENSE_PUBLIC_KEY="", EE_LICENSE_PUBLIC_KEYS=attacker_keys
            ):
                load_keyring_from_settings()

            assert keyring.get_key("attacker-kid") is None
            assert keyring.get_key("prod-2026") == _BUNDLED

    def test_env_keys_loaded_when_nothing_bundled(self):
        # Pre-GA / dev escape hatch: with no bundled trust root, env keys are
        # the only available trust source and ARE loaded.
        with patch.object(keyring, "_BUNDLED_KEYS", ()):
            with override_settings(
                EE_LICENSE_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\ndev\n-----END PUBLIC KEY-----",
                EE_LICENSE_PUBLIC_KEYS="",
            ):
                load_keyring_from_settings()

            assert keyring.get_key("default") is not None

    def test_env_key_cannot_replace_bundled_kid(self):
        attacker_keys = (
            '[{"kid": "prod-2026", "algorithm": "RS256",'
            ' "public_key": "-----BEGIN PUBLIC KEY-----\\nattacker\\n-----END PUBLIC KEY-----"}]'
        )
        with patch.object(keyring, "_BUNDLED_KEYS", (_BUNDLED,)):
            with override_settings(
                EE_LICENSE_PUBLIC_KEY="", EE_LICENSE_PUBLIC_KEYS=attacker_keys
            ):
                load_keyring_from_settings()

            entry = keyring.get_key("prod-2026")
            assert entry == _BUNDLED, "env key overrode the bundled trust root"

    def test_settings_failure_falls_back_to_bundled_not_empty(self):
        with patch.object(keyring, "_BUNDLED_KEYS", (_BUNDLED,)):
            with override_settings(
                EE_LICENSE_PUBLIC_KEY="", EE_LICENSE_PUBLIC_KEYS="not-json"
            ):
                load_keyring_from_settings()

            assert keyring.get_key("prod-2026") == _BUNDLED
            assert keyring.has_any_keys()


class TestUnlicensedManagedTransport:
    """Unlicensed/OSS deployments must fall through to litellm + env keys:
    the managed path only claims managed models, and only on EE mode."""

    def test_non_managed_model_never_uses_managed_path(self):
        from ee.licensing.managed_ai import is_managed_model

        assert is_managed_model("vertex_ai/gemini-2.5-pro") is False
        assert is_managed_model("gpt-4o") is False
        assert is_managed_model("turing_large") is True
        assert is_managed_model("falcon_ai") is True

    def test_llm_does_not_require_managed_transport_off_ee(self):
        from agentic_eval.core.llm.llm import LLM

        with patch("ee.usage.deployment.DeploymentMode.is_ee", return_value=False):
            llm = LLM.__new__(LLM)
            llm.model_name = "turing_large"
            assert llm._requires_managed_transport() is False

    def test_llm_requires_managed_transport_for_managed_model_on_ee(self):
        from agentic_eval.core.llm.llm import LLM

        with patch("ee.usage.deployment.DeploymentMode.is_ee", return_value=True):
            llm = LLM.__new__(LLM)
            llm.model_name = "vertex_ai/gemini-2.5-pro"
            # ordinary models never demand the managed transport, even on EE
            assert llm._requires_managed_transport() is False
            assert llm._requires_managed_transport("turing_large") is True
