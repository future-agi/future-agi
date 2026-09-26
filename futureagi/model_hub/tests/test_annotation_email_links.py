"""Annotation emails link to this install, not to Future AGI Cloud.

FRONTEND_URL / BACKEND_URL are optional; without them a self-hosted install
used to link digests, unsubscribe pages and rule-run results to
app.futureagi.com and api.futureagi.com. No database needed.
"""

from unittest.mock import MagicMock, patch

import pytest

from model_hub.utils import annotation_digest
from model_hub.utils.annotation_queue_helpers import send_rule_completion_email


@pytest.fixture(autouse=True)
def self_hosted_urls(settings):
    settings.APP_BASE_URL = "http://localhost:3000"
    settings.BASE_URL = "http://localhost:8000"


@pytest.fixture
def no_url_overrides(monkeypatch):
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.delenv("BACKEND_URL", raising=False)


@pytest.mark.unit
class TestDigestLinks:
    def test_default_to_this_install(self, no_url_overrides):
        assert annotation_digest._frontend_url() == "http://localhost:3000"
        assert annotation_digest._backend_url() == "http://localhost:8000"

    def test_explicit_urls_win(self, monkeypatch):
        monkeypatch.setenv("FRONTEND_URL", "https://ai.example.com/")
        monkeypatch.setenv("BACKEND_URL", "https://api.example.com/")

        assert annotation_digest._frontend_url() == "https://ai.example.com"
        assert annotation_digest._backend_url() == "https://api.example.com"


@pytest.mark.unit
def test_rule_run_email_links_to_this_install(no_url_overrides):
    rule = MagicMock(pk=1, source_type="trace")
    rule.name = "Low scores"
    rule.queue.id = "queue-1"
    rule.queue.name = "Review"

    with (
        patch(
            "model_hub.utils.annotation_queue_helpers._rule_completion_recipients",
            return_value=["ada@example.com"],
        ),
        patch("tfc.utils.email.email_helper") as email_helper,
    ):
        send_rule_completion_email(rule, {"added": 1})

    template_data = email_helper.call_args.kwargs["template_data"]
    assert template_data["queue_url"] == (
        "http://localhost:3000/annotation-queues/queue-1"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "base, expected_prefix",
    [
        ("http://localhost:3000", "http://localhost:3000/"),
        ("https://ai.example.com", "https://ai.example.com/"),
        ("", "/"),
    ],
)
def test_discussion_link_keeps_the_install_scheme(settings, base, expected_prefix):
    # A loopback UI stays http even under ENV_TYPE=production (Helm port-forward).
    from model_hub.views.annotation_queues import _annotation_discussion_url

    settings.APP_BASE_URL = base
    url = _annotation_discussion_url(MagicMock(queue_id="q1", id="i1"))

    assert url == f"{expected_prefix}dashboard/annotations/queues/q1/annotate?itemId=i1"
