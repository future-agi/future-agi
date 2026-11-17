"""Gateway deployment-key inventory, including inactive and soft-deleted rows.

Only these encrypted stores use this transition, including webhook v1 envelopes.
API-key/refresh-token hashes and push-time identity secrets are not Fernet stores.
Shared integrations/model-hub stores are deliberately NOT covered;
this inventory can never authorize retiring the shared deployment key.
"""

import copy
from collections import Counter
from dataclasses import dataclass

from django.apps import apps
from django.core.management.base import CommandError

from agentcc.services.org_config_secrets import iter_secret_slots

# Exact model/field allowlist, checked against current concrete model metadata.
_STORES = (
    ("AgentccProviderCredential", ("encrypted_credentials",), False),
    ("AgentccGuardrailPolicy", ("encrypted_check_configs",), False),
    ("AgentccEmailAlert", ("encrypted_config",), False),
    ("AgentccWebhook", ("encrypted_headers", "encrypted_secret"), False),
    ("AgentccManagedUpstreamKey", ("encrypted_key",), True),
    ("AgentccMCPCredential", ("encrypted_secret", "encrypted_refresh"), True),
    ("AgentccMCPOAuthSigningKey", ("encrypted_private",), True),
    # EncryptedAtRestModel fields use encrypt_token's enc:: / {"v": ...} wrapper.
    ("AgentccMCPVendorBinding", ("encrypted_bearer",), True),
    ("AgentccMCPVendorConnection", ("encrypted_session", "encrypted_endpoint"), True),
    ("AgentccMCPVendorCleanup", ("encrypted_session",), True),
    ("AgentccSIEMDestination", ("encrypted_secret",), True),
    ("AgentccMCPConnectState", ("encrypted_verifier",), True),
)


@dataclass(frozen=True)
class Store:
    model: type
    fields: tuple
    wrapped: bool = False
    config: bool = False

    def transform(self, row, crypto):
        values = {name: getattr(row, name) for name in self.fields}
        if self.config:
            converted, counts = transform_config(values, crypto)
        else:
            counts = Counter()
            converted = {}
            for name, value in values.items():
                field = self.model._meta.get_field(name)
                converted[name], kind = crypto.convert(
                    value,
                    wrapped=self.wrapped,
                    nullable=field.null,
                    blank=field.blank,
                )
                counts[kind] += 1
        return {
            name: value for name, value in converted.items() if value != values[name]
        }, counts


def inventory():
    models = {
        model.__name__: model for model in apps.get_app_config("prism").get_models()
    }
    actual = {
        (name, field.name)
        for name, model in models.items()
        for field in model._meta.concrete_fields
        if field.name.startswith("encrypted_")
        or field.get_internal_type() == "BinaryField"
    }
    expected = {(name, field) for name, fields, _ in _STORES for field in fields}
    if actual != expected:
        raise CommandError("Gateway encryption inventory changed; review required")
    stores = [Store(models[name], fields, wrapped) for name, fields, wrapped in _STORES]
    config = models["AgentccOrgConfig"]
    # Read every JSON field to refuse enc:: values outside the shared slot list.
    fields = tuple(
        field.name
        for field in config._meta.concrete_fields
        if field.get_internal_type() == "JSONField"
    )
    stores.append(Store(config, fields, config=True))
    return tuple(stores)


def transform_config(values, crypto):
    converted = copy.deepcopy(values)
    slots = list(iter_secret_slots(converted))
    # The shared slot owns a live container/key; object identity avoids ambiguous
    # slash-containing JSON keys when identifying supported secret locations.
    locations = {(id(slot._container), slot._key) for slot in slots}

    def refuse_unknown(node):
        entries = (
            node.items()
            if isinstance(node, dict)
            else enumerate(node)
            if isinstance(node, list)
            else ()
        )
        for key, value in entries:
            if (
                isinstance(value, str)
                and value.startswith("enc::")
                and (id(node), key) not in locations
            ):
                raise CommandError(
                    "Gateway config encryption inventory changed; review required"
                )
            refuse_unknown(value)

    refuse_unknown(converted)
    counts = Counter()
    for slot in slots:
        value, kind = crypto.convert(
            slot.value, wrapped=True, nullable=True, blank=True
        )
        slot.set(value)
        counts[kind] += 1
    return converted, counts
