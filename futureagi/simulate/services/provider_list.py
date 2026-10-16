from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from simulate.models import ProviderList

PROVIDER_LIST_CACHE_SECONDS = 60
SDK_VERSION_MAX_LENGTH = 32

_cache: dict[str | None, tuple[float, ProviderListSnapshot]] = {}


class ProviderListError(Exception):
    pass


class ProviderListNotPublished(ProviderListError):
    pass


class ProviderListVersionNotFound(ProviderListError):
    pass


class ProviderListDocumentInvalid(ProviderListError):
    pass


@dataclass(frozen=True)
class AskField:
    name: str
    label: str
    secret: bool
    required: bool


@dataclass(frozen=True)
class ProviderEntry:
    provider: str
    display_name: str
    auth_strategy: str
    ask: tuple[AskField, ...]
    capabilities: tuple[str, ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ProviderListSnapshot:
    sdk_version: str
    content_hash: str
    published_at: datetime
    providers: Mapping[str, ProviderEntry]
    agent_schema: Mapping[str, Any]
    episode_export: Mapping[str, Any]
    document: Mapping[str, Any]


def read_provider_list(*, sdk_version: str | None = None) -> ProviderListSnapshot:
    if sdk_version is not None and (
        not isinstance(sdk_version, str)
        or not 1 <= len(sdk_version) <= SDK_VERSION_MAX_LENGTH
    ):
        raise ValueError("sdk_version must be a string of 1 to 32 characters or None")
    cached = _cache.get(sdk_version)
    if (
        cached is not None
        and time.monotonic() - cached[0] < PROVIDER_LIST_CACHE_SECONDS
    ):
        return cached[1]
    rows = ProviderList.no_workspace_objects
    if sdk_version is None:
        row = rows.order_by("-last_published_at", "-sdk_version").first()
        if row is None:
            raise ProviderListNotPublished("provider list not published")
    else:
        row = rows.filter(sdk_version=sdk_version).first()
        if row is None:
            raise ProviderListVersionNotFound(
                f"provider list not published for sdk {sdk_version}"
            )
    snapshot = _snapshot(row)
    _cache[sdk_version] = (time.monotonic(), snapshot)
    return snapshot


def _snapshot(row: ProviderList) -> ProviderListSnapshot:
    document = row.document
    providers = {
        entry["provider"]: ProviderEntry(
            provider=entry["provider"],
            display_name=entry["display_name"],
            auth_strategy=entry["auth_strategy"],
            ask=tuple(
                AskField(
                    name=field["name"],
                    label=field["label"],
                    secret=field["secret"],
                    required=field["required"],
                )
                for field in entry["ask"]
            ),
            capabilities=tuple(entry["capabilities"]),
            raw=entry,
        )
        for entry in document["providers"]
    }
    return ProviderListSnapshot(
        sdk_version=row.sdk_version,
        content_hash=row.content_hash,
        published_at=row.last_published_at,
        providers=MappingProxyType(providers),
        agent_schema=document["agent_schema"],
        episode_export=document["episode_export"],
        document=document,
    )
