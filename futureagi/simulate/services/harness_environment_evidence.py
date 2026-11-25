"""Read-only joins of the source schema and measured environment stores."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from simulate.services.hosted_harness_diagnostics import _redact

INTERNAL_TABLES = frozenset({"_alk_tool_trace", "harness_seed_sentinel"})


def _secret_safe_url(value: str, *, schema: bool = False) -> str:
    address = value.rstrip(".,;!?)")
    try:
        url = urlsplit(address)
        if not url.scheme or not url.netloc:
            return value
        query = (
            urlencode(
                [
                    (key, item)
                    for key, item in parse_qsl(url.query)
                    if key in {"version", "v"}
                    and re.fullmatch(r"v?[0-9]{1,8}(?:[.-][0-9]{1,8}){0,3}", item)
                ]
            )
            if schema
            else ""
        )
        return (
            urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, query, ""))
            + value[len(address) :]
        )
    except ValueError:
        return "[redacted]"


def _secret_safe(value: Any, *, key: str = "", schema: bool = False) -> Any:
    """Return an authoring document safe to persist and display.

    Authoring outputs should normally contain references rather than secret values. This is a
    second boundary check so a malformed/generated environment cannot leak a credential into the
    job JSON or API response.
    """
    normalized = key.lower()
    safe_secret_metadata = {
        "secret_purposes",
        "secret_refs",
        "required_secrets",
        "required_credentials",
    }
    secret_markers = (
        "api_key",
        "api_secret",
        "password",
        "private_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "credential_json",
        "secret_value",
    )
    sensitive = normalized not in safe_secret_metadata and (
        normalized == "secrets"
        or normalized.endswith("_secret")
        or any(marker in normalized for marker in secret_markers)
    )
    if sensitive and value in ("", None):
        return value
    if sensitive and not (schema and isinstance(value, dict)):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): "[redacted]"
            if sensitive
            and item_key in {"default", "example", "examples", "const", "enum"}
            else _secret_safe(
                item_value,
                key=str(item_key),
                schema=schema
                or normalized in {"parameters", "input_schema", "output_schema"},
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_secret_safe(item, schema=schema) for item in value]
    if (
        isinstance(value, str)
        and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value)
        and not any(char.isspace() for char in value)
    ):
        return _secret_safe_url(value, schema=schema)
    if isinstance(value, str):
        value = re.sub(
            r"https?://[^\s<>\"'`]+",
            lambda match: _secret_safe_url(match.group()),
            value,
            flags=re.IGNORECASE,
        )
        value = _redact(value, ())
        value = re.sub(r"(\w+://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class EnvironmentEvidence:
    contract: dict[str, Any]
    environment: dict[str, Any] | None
    scenarios: list[dict[str, Any]] | None
    sub_goals: dict[str, Any] | None
    stores: list[dict[str, Any]] | None
    source_model: dict[str, Any] | None
    tool_certification: dict[str, Any] | None

    def schema_tables(self) -> list[dict[str, Any]] | None:
        if self.source_model is None:
            return None
        tables = []
        for table in self.source_model.get("tables", []):
            primary_key = table.get("primary_key", [])
            foreign_keys = table.get("foreign_keys", [])
            columns = []
            for column in table.get("columns", []):
                references = [
                    f"{key['referenced_table']}.{key['referenced_columns'][0]}"
                    for key in foreign_keys
                    if key.get("columns") == [column["name"]]
                    and len(key.get("referenced_columns", [])) == 1
                ]
                columns.append(
                    {
                        "name": column["name"],
                        "type": column["native_type"],
                        "enum": column.get("enum_values", []),
                        "pk": column["name"] in primary_key,
                        "nullable": column["nullable"],
                        "references": references[0] if len(references) == 1 else None,
                    }
                )
            tables.append(
                {
                    "name": table["name"],
                    "columns": columns,
                    "primary_key": primary_key,
                    "foreign_keys": foreign_keys,
                }
            )
        return tables

    def store_tables(
        self, schema_tables: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        stores = self.stores or []
        schema = (
            {table["name"]: table["columns"] for table in schema_tables}
            if schema_tables is not None
            else self.contract.get("data_schema_columns") or {}
        )
        # shortcut: SourceModel lacks store identity; join multiple stores when it records one.
        can_join = len(stores) == 1
        result = []
        for store in stores:
            tables = []
            for table in store.get("tables", []):
                name = table["name"]
                columns = schema.get(name) if can_join else None
                if can_join and columns is None and "." not in name:
                    matches = [
                        value
                        for identity, value in schema.items()
                        if identity.rsplit(".", 1)[-1] == name
                    ]
                    if len(matches) == 1:
                        columns = matches[0]
                tables.append(
                    {
                        **table,
                        "internal": name in INTERNAL_TABLES,
                        "columns": columns,
                    }
                )
            result.append({**store, "tables": tables})
        return result


def has_verified_system_prompt(contract: dict[str, Any]) -> bool:
    prompt = contract.get("system_prompt")
    origin = contract.get("system_prompt_origin")
    return (
        isinstance(prompt, str)
        and isinstance(origin, dict)
        and origin.get("sha256") == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    )


def environment_detail_v2(
    detail: dict[str, Any], entrypoint_details: list[dict[str, Any]]
) -> dict[str, Any]:
    """Change response shapes using only the already projected evidence."""
    original = detail.get("contract")
    if original is None:
        return {**detail, "contract_version": 2}
    contract = dict(original)
    for field, companion in (
        ("hard_constraints", "hard_constraint_details"),
        ("real_use_cases", "real_use_case_details"),
    ):
        details = contract.get(companion) or []
        texts = contract.get(field)
        if texts is None:
            contract[field] = details or None
            continue
        items = []
        for text in texts:
            matches = [item for item in details if item["text"] == text]
            items.append(dict(matches[0]) if len(matches) == 1 else {"text": text})
        contract[field] = items
    contract["data_schema_legacy"] = contract.get("data_schema")
    contract["data_schema"] = contract.get("data_schema_columns")
    store = contract.get("data_store")
    if store is not None:
        contract["data_store"] = {
            **store,
            "configured_by_legacy": store.get("configured_by"),
            "configured_by": store.get("configuration"),
        }
    contract["amendments_legacy"] = contract["amendments"]
    amendments = [dict(item) for item in contract.get("amendment_details") or []]
    documented = {(item["subject"], item["reason"]) for item in amendments}
    contract["amendments"] = amendments + [
        {"subject": item["subject"], "kind": None, "reason": item["note"]}
        for item in contract["amendments"]
        if (item["subject"], item["note"]) not in documented
    ]
    tools = contract.get("tools")
    if tools is not None:
        contract["tools"] = [
            {
                **tool,
                "effect": tool.get("effect_kind"),
                "effect_legacy": tool.get("effect"),
            }
            for tool in tools
        ]
    entries = contract.get("tool_entrypoints")
    if entries is not None or entrypoint_details:
        by_tool = {entry["tool"]: entry for entry in entries or []}
        for entry in entrypoint_details:
            existing = by_tool.get(entry["tool"])
            if existing is None or (
                existing.get("mode") == "unreachable"
                and not existing.get("source")
                and existing.get("implementation_origin") != "source"
            ):
                by_tool[entry["tool"]] = entry
        contract["tool_entrypoints"] = [
            {
                **entry,
                "origin": entry.get("implementation_origin"),
                "origin_evidence": entry.get("origin"),
            }
            for entry in by_tool.values()
        ]
    return {**detail, "contract_version": 2, "contract": contract}
