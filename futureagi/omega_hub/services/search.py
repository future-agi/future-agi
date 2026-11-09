"""Discovery (SPEC §7.3): full-text search, filters, the four sorts, and the publishers index."""

from __future__ import annotations

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import Count, Q

from omega_hub.errors import HubError
from omega_hub.models import Entry, Publisher
from omega_hub.services import counts

SORTS = ("trending", "new", "likes", "installs")
KINDS = ("app", "agent", "team", "skill", "plugin", "mcp", "workflow")


def _base():
    return Entry.objects.select_related("publisher", "latest_version").filter(
        status=Entry.Status.LISTED, latest_version__isnull=False
    )


def _approved_at(entry) -> float:
    return entry.latest_version.approved_at.timestamp() if entry.latest_version else 0.0


def _filtered(params: dict):
    qs = _base()
    if params.get("kind"):
        if params["kind"] not in KINDS:
            raise HubError(
                "OMEGA_HUB_INVALID", 400, f"kind is one of: {', '.join(KINDS)}"
            )
        qs = qs.filter(kind=params["kind"])
    if params.get("vendor"):
        qs = qs.filter(vendors__contains=[params["vendor"]])
    if params.get("noKey") in ("1", "true"):
        qs = qs.filter(no_key_needed=True)
    if params.get("tag"):
        qs = qs.filter(tags__contains=[params["tag"]])
    if params.get("tier"):
        qs = qs.filter(publisher__tier=params["tier"])
    if params.get("publisher"):
        qs = qs.filter(publisher__handle=params["publisher"])
    q = (params.get("q") or "").strip()
    if q:
        query = SearchQuery(q, config="english", search_type="websearch")
        vector = SearchVector("search_text", config="english")
        # Match with @@, never rank > 0 (non-matching phrases can have a tiny
        # positive rank). Browse and cross-kind facets must use this same predicate.
        qs = qs.annotate(search_vector=vector, rank=SearchRank(vector, query)).filter(
            Q(search_vector=query)
            | Q(name__icontains=q)
            | Q(publisher__handle__icontains=q)
        )
    return qs


def default_sort(entries: list[Entry]) -> str:
    """The order of a browse that names no sort (HUB-24): Trending once any of these entries has a
    Trending score above zero, New until then, because Trending over all-zero counts is an arbitrary
    order (launch day shows every count as zero)."""
    scores = counts.trending_scores()
    return (
        "trending" if any(scores.get(entry.pk, 0) > 0 for entry in entries) else "new"
    )


def search_entries(params: dict) -> list[Entry]:
    return browse(params)[0]


def browse(params: dict) -> tuple[list[Entry], str]:
    """(entries, the order applied): one of SORTS, or "relevance" for a text search."""
    sort = params.get("sort") or None
    if sort is not None and sort not in SORTS:
        raise HubError("OMEGA_HUB_INVALID", 400, f"sort is one of: {', '.join(SORTS)}")
    qs = _filtered(params)
    q = (params.get("q") or "").strip()
    if q:
        entries = list(qs)
        # Ranked entries first, by relevance; unranked ones after every ranked match (SPEC §3.3).
        return sorted(
            entries, key=lambda e: (not e.ranked, -(e.rank or 0), -_approved_at(e))
        ), "relevance"
    entries = [entry for entry in qs if entry.ranked]
    sort = sort or default_sort(entries)
    return _sort(entries, sort), sort


def _sort(entries: list[Entry], sort: str) -> list[Entry]:
    if sort == "new":
        return sorted(
            entries,
            key=lambda e: (
                -(e.first_published_at.timestamp() if e.first_published_at else 0)
            ),
        )
    if sort == "likes":
        return sorted(entries, key=lambda e: (-e.like_count, -_approved_at(e)))
    if sort == "installs":
        installs = counts.downloads_by_entry(30)
        return sorted(entries, key=lambda e: (-installs.get(e.pk, 0), -_approved_at(e)))
    scores = counts.trending_scores()
    return sorted(entries, key=lambda e: (-scores.get(e.pk, 0), -_approved_at(e)))


def kind_counts(params: dict) -> dict:
    params = {
        key: value
        for key, value in params.items()
        if key not in ("kind", "sort", "page", "page_size")
    }
    qs = _filtered(params)
    if not (params.get("q") or "").strip():
        qs = qs.filter(ranked=True)
    found = dict(qs.values_list("kind").annotate(n=Count("id")))
    return {kind: found.get(kind, 0) for kind in KINDS}


def tag_counts() -> list[dict]:
    totals: dict[str, int] = {}
    for tags in _base().values_list("tags", flat=True):
        for tag in tags:
            totals[tag] = totals.get(tag, 0) + 1
    return [
        {"tag": tag, "count": n}
        for tag, n in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def remixes_of(entry: Entry) -> list[Entry]:
    return list(
        _base().filter(latest_version__remix_of__entry=entry).exclude(pk=entry.pk)
    )


def publisher_totals(publishers) -> dict:
    """Per publisher: listed entries, likes and 30-day installs over ranked entries (SPEC §3.3)."""
    ids = [p.pk for p in publishers]
    entries = list(_base().filter(publisher_id__in=ids))
    installs = counts.downloads_by_entry(30, entry_ids=[e.pk for e in entries])
    totals = {pid: {"entryCount": 0, "likes": 0, "installs30d": 0} for pid in ids}
    for entry in entries:
        row = totals[entry.publisher_id]
        row["entryCount"] += 1
        if entry.ranked:
            row["likes"] += entry.like_count
            row["installs30d"] += installs.get(entry.pk, 0)
    return totals


def list_publishers(params: dict) -> tuple[list[Publisher], dict]:
    qs = Publisher.objects.filter(
        first_published_at__isnull=False, suspended_at__isnull=True
    )
    if params.get("tier"):
        qs = qs.filter(tier=params["tier"])
    publishers = list(qs)
    totals = publisher_totals(publishers)
    if (params.get("sort") or "installs") == "new":
        publishers.sort(key=lambda p: -p.first_published_at.timestamp())
    else:
        publishers.sort(key=lambda p: (-totals[p.pk]["installs30d"], p.handle))
    return publishers, totals
