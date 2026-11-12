"""The chart's omega.hostEvents.url cases agree with the sender's own URL check (item 157).

deploy/helm/futureagi/hack/test_omega_host_events_chart.py proves `helm template` refuses every
url in SENDER_REFUSES and renders every url in BOTH_ACCEPT. This proves the sender
(accounts/host_events/config.py _check_url) refuses the first list and accepts the second, so a
url the sender would refuse cannot pass the chart and leave a pod MISCONFIGURED. The chart test
checks the same where it can import this module; Helm CI has PyYAML only, so it runs here too.
"""

import importlib.util
from pathlib import Path

import pytest

from accounts.host_events.config import _check_url

CHART_TEST = (
    Path(__file__).resolve().parents[3]
    / "deploy/helm/futureagi/hack/test_omega_host_events_chart.py"
)


@pytest.fixture(scope="module")
def cases():
    if not CHART_TEST.is_file():
        pytest.skip("the Helm chart is not in this checkout")
    spec = importlib.util.spec_from_file_location("omega_host_events_chart", CHART_TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _accepted(url):
    try:
        _check_url(url, False)
    except ValueError:
        return False
    return True


def test_every_url_the_chart_is_shown_to_refuse_for_the_sender_is_refused_by_it(cases):
    assert cases.SENDER_REFUSES
    for url in cases.SENDER_REFUSES:
        assert not _accepted(url), url


def test_every_url_the_chart_renders_is_accepted_by_the_sender(cases):
    assert cases.BOTH_ACCEPT
    for url in cases.BOTH_ACCEPT:
        assert _accepted(url), url
