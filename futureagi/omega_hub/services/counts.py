"""Install and open counts (SPEC §6.2) and the Trending score (§7.3).

Only `DownloadDaily(version, day, source, count)` is stored. The per-day de-duplication fingerprint,
sha256(salt | address | day) cut to 16 hex characters, lives only as a cache key for 24 hours. The
address is the one the Hub trusts (`client_ip`, HUB-04); the User-Agent is not part of it, because a
client sets it freely. One address also counts at most `conf.counted_downloads_per_address()`
downloads a day across every version, so it cannot raise Trending by fetching many versions.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from omega_hub import conf, rules
from omega_hub.client_ip import client_ip
from omega_hub.models import DownloadDaily, Like, Version
from omega_hub.services import cache_keys

INSTALL_SOURCES = ("cli",)
OPEN_SOURCES = ("studio", "deploy", "console")
DEDUPE_SECONDS = 86400


def fingerprint(request, day: date) -> str:
    salt = settings.SECRET_KEY if isinstance(settings.SECRET_KEY, str) else settings.SECRET_KEY.decode()
    raw = "|".join([salt, client_ip(request), day.isoformat()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _address_allows(address_key: str) -> bool:
    """Take one of the address's counted downloads for the day; False once they are used up."""
    cap = conf.counted_downloads_per_address()
    if cache.add(address_key, 1, DEDUPE_SECONDS):
        return cap >= 1
    try:
        return cache.incr(address_key) <= cap
    except ValueError:  # expired between add and incr
        cache.add(address_key, 1, DEDUPE_SECONDS)
        return cap >= 1


def _increment(version: Version, day: date, source: str) -> None:
    with transaction.atomic():
        if DownloadDaily.objects.filter(version=version, day=day, source=source).update(count=F("count") + 1):
            return
        try:
            with transaction.atomic():
                DownloadDaily.objects.create(version=version, day=day, source=source, count=1)
        except IntegrityError:
            DownloadDaily.objects.filter(version=version, day=day, source=source).update(count=F("count") + 1)


def count_download(version: Version, request, source: str, *, session: bool) -> bool:
    """Count one download; `opens` only for a signed-in Future AGI session. Returns whether it counted."""
    if source not in INSTALL_SOURCES and not (source in OPEN_SOURCES and session):
        return False
    day = timezone.now().date()
    who = fingerprint(request, day)
    key = f"omega_hub:dl:{version.pk}:{source}:{who}"
    if cache.get(key) is not None:
        return False
    if not _address_allows(f"omega_hub:dl-addr:{who}"):
        return False
    if not cache.add(key, 1, DEDUPE_SECONDS):
        return False
    _increment(version, day, source)
    cache.delete(cache_keys.TRENDING)
    return True


def _since(days: int) -> date:
    return timezone.now().date() - timedelta(days=days - 1)


def downloads_by_entry(days: int, sources=INSTALL_SOURCES, entry_ids=None) -> dict:
    rows = DownloadDaily.objects.filter(day__gte=_since(days), source__in=sources)
    if entry_ids is not None:
        rows = rows.filter(version__entry_id__in=entry_ids)
    return {row["version__entry_id"]: row["n"] for row in rows.values("version__entry_id").annotate(n=Sum("count"))}


def likes_by_entry(days: int, entry_ids=None) -> dict:
    rows = Like.objects.filter(created_at__date__gte=_since(days))
    if entry_ids is not None:
        rows = rows.filter(entry_id__in=entry_ids)
    return {row["entry_id"]: row["n"] for row in rows.values("entry_id").annotate(n=Count("id"))}


def daily_series(entry_ids, days: int = 30) -> dict:
    start = _since(days)
    series = {entry_id: [0] * days for entry_id in entry_ids}
    rows = DownloadDaily.objects.filter(day__gte=start, source__in=INSTALL_SOURCES, version__entry_id__in=list(entry_ids))
    for row in rows.values("version__entry_id", "day").annotate(n=Sum("count")):
        series[row["version__entry_id"]][(row["day"] - start).days] = row["n"]
    return series


def trending_scores() -> dict:
    """`installs_7d * w1 + likes_7d * w3`, weights from the rules file, cached 5 minutes."""
    cached = cache.get(cache_keys.TRENDING)
    if cached is not None:
        return cached
    weights = rules.trending_weights()
    installs = downloads_by_entry(7)
    likes = likes_by_entry(7)
    scores = {
        entry_id: installs.get(entry_id, 0) * weights.get("installs7d", 1) + likes.get(entry_id, 0) * weights.get("likes7d", 3)
        for entry_id in set(installs) | set(likes)
    }
    cache.set(cache_keys.TRENDING, scores, 300)
    return scores
