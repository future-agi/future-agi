"""Reviewer-visible citations on indexed scenario rows."""

import json
from collections import defaultdict

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from simulate.models import HarnessSourceReview
from simulate.services.harness_source_summary import (
    _accepted_checkpoint,
    resolve_legacy_filenames,
)


def _locations(case):
    if not isinstance(case, dict) or not isinstance(case.get("citations"), list):
        return []
    result = []
    for item in case["citations"]:
        if not isinstance(item, dict) or not isinstance(item.get("source_id"), str):
            continue
        location = {"source_id": item["source_id"]}
        for field in ("page", "row"):
            value = item.get(field)
            if type(value) is int and value > 0:
                location[field] = value
        if location not in result:
            result.append(location)
    return result


def indexed_locations(doc):
    fixture = doc.get("fixture")
    if isinstance(fixture, dict):
        return _locations(fixture.get("reviewed_source")) or None
    return None


def _legacy_locations(row):
    _, marker, body = (row.tests or "").rpartition("\n\nReviewed source expectations: ")
    if marker:
        try:
            return _locations(json.loads(body))
        except ValueError:
            pass
    return []


def visible_citations(request, job, page):
    locations = {
        row.scenario_key: row.reviewed_source_locations or _legacy_locations(row)
        for row in page
    }
    source_ids = {item["source_id"] for items in locations.values() for item in items}
    if not source_ids:
        return {}

    from simulate.services.harness_source_reviews import require_source_scope
    from simulate.services.hosted_harness import HostedHarnessError

    try:
        scope = require_source_scope(request, write=False)
    except (HostedHarnessError, PermissionDenied):
        return {}

    checkpoint = _accepted_checkpoint(job, scope)
    matching_sources = Q()
    for source_id in sorted(source_ids):
        matching_sources |= Q(
            preview__sources__contains=[{"pin": {"source_id": source_id}}]
        )
    reviews = list(
        HarnessSourceReview.objects.filter(
            matching_sources,
            **scope,
            owner=job,
            owner_kind=HarnessSourceReview.Owner.ENVIRONMENT,
            state=HarnessSourceReview.State.ACCEPTED,
        ).only("preview")
    )
    if checkpoint:
        reviews.append(checkpoint)
    resolve_legacy_filenames(reviews, scope)
    candidates = defaultdict(set)
    for review in reviews:
        for source in review.preview.get("sources", []):
            source_id = source["pin"]["source_id"]
            if source_id in source_ids:
                filename = (
                    source.get("filename")
                    if source.get("visibility") == "agent_visible"
                    else None
                )
                candidates[source_id].add(filename)
    names = {
        key: name
        for key, choices in candidates.items()
        if len(choices) == 1
        for name in choices
        if name
    }
    return {
        key: [
            {
                "filename": names[item["source_id"]],
                **{k: v for k, v in item.items() if k in ("page", "row")},
            }
            for item in items
            if item["source_id"] in names
        ]
        for key, items in locations.items()
        if items
    }
