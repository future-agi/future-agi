"""HUB-24: while every count is zero the Hub opens on New, not on Trending over zeros; the response
says which order it applied, so the page highlights the right tab."""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from omega_hub.models import Entry
from omega_hub.tests.flows import publish

pytestmark = pytest.mark.django_db
ENTRIES = "/omega-hub/v1/entries"


def _refs(body):
    return [card["ref"] for card in body["results"]]


def _three_entries(world):
    first = publish(world)
    second = publish(world, "notes-plugin")
    third = publish(world, "engineering-team")
    now = timezone.now()
    for age, entry in ((3, first), (2, second), (1, third)):  # engineering-team is the newest
        Entry.objects.filter(pk=entry.pk).update(first_published_at=now - timezone.timedelta(days=age))
    return first, second, third


def test_with_every_count_at_zero_the_default_is_new(world):
    _three_entries(world)
    body = APIClient().get(ENTRIES).json()
    assert body["sort"] == "new"
    assert _refs(body) == ["future-agi/engineering-team", "future-agi/notes-plugin", "future-agi/first-agent"]
    assert all(card["likes"] == 0 for card in body["results"])


def test_once_anything_is_counted_the_default_is_trending(world):
    _three_entries(world)
    assert world.c.post("/omega-hub/v1/entries/future-agi/first-agent/like").status_code == 200
    body = APIClient().get(ENTRIES).json()
    assert body["sort"] == "trending"
    assert _refs(body)[0] == "future-agi/first-agent"


def test_an_install_alone_also_switches_to_trending(world):
    _three_entries(world)
    APIClient().get("/omega-hub/v1/entries/future-agi/notes-plugin/versions/1.0.0/bundle", REMOTE_ADDR="203.0.113.70")
    body = APIClient().get(ENTRIES).json()
    assert body["sort"] == "trending" and _refs(body)[0] == "future-agi/notes-plugin"


def test_an_explicit_sort_is_kept_and_a_search_is_by_relevance(world):
    _three_entries(world)
    assert APIClient().get(ENTRIES, {"sort": "trending"}).json()["sort"] == "trending"
    assert APIClient().get(ENTRIES, {"sort": "likes"}).json()["sort"] == "likes"
    assert APIClient().get(ENTRIES, {"q": "notes"}).json()["sort"] == "relevance"
    bad = APIClient().get(ENTRIES, {"sort": "random"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "OMEGA_HUB_INVALID"


def test_counts_on_an_entry_that_is_not_listed_do_not_switch_the_default(world):
    first, _, _ = _three_entries(world)
    world.c.post("/omega-hub/v1/entries/future-agi/first-agent/like")
    Entry.objects.filter(pk=first.pk).update(ranked=False)
    assert APIClient().get(ENTRIES).json()["sort"] == "new"
