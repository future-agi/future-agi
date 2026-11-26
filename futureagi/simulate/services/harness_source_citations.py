"""Reviewer-visible citations on indexed scenario rows."""

import json

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
    if not any(locations.values()):
        return {}

    from simulate.services.harness_source_reviews import require_source_scope
    from simulate.services.hosted_harness import HostedHarnessError

    try:
        scope = require_source_scope(request, write=False)
    except (HostedHarnessError, PermissionDenied):
        return {}

    checkpoint = _accepted_checkpoint(job, scope)
    reviews = list(
        HarnessSourceReview.objects.filter(
            **scope,
            owner=job,
            owner_kind=HarnessSourceReview.Owner.ENVIRONMENT,
            state=HarnessSourceReview.State.ACCEPTED,
        ).only("preview")
    )
    if checkpoint:
        reviews.append(checkpoint)
    resolve_legacy_filenames(reviews, scope)
    names = {
        source["pin"]["source_id"]: source["filename"]
        for review in reviews
        for source in review.preview.get("sources", [])
        if source.get("filename") and source.get("visibility") == "agent_visible"
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
