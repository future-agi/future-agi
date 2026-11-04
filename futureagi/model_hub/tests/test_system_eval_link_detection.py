"""Link-detection system evals must not treat e-mail addresses or
number/abbreviation tokens as URLs.

The previous pattern's ``(?!.*@)`` guard only rejected match attempts that
start before an ``@`` — ``re.search``/``re.findall`` simply retried after it
and matched the e-mail's domain. ``\\S+\\.\\S+`` also matched decimals
("3.14") and abbreviations ("e.g."), so ``no_invalid_links`` HEAD-requested
``http://3.14`` and failed link-free outputs.
"""

from pathlib import Path
from unittest import mock

import yaml

SYSTEM_EVALS_DIR = Path(__file__).resolve().parents[1] / "system_evals" / "function"


def _load_evaluate(yaml_name):
    definition = yaml.safe_load(
        (SYSTEM_EVALS_DIR / yaml_name).read_text(encoding="utf-8")
    )
    namespace = {}
    exec(definition["config"]["code"], namespace)  # noqa: S102 — repo-owned YAML
    return namespace["evaluate"]


def _response(status=200):
    resp = mock.Mock()
    resp.status = status
    return resp


class TestContainsValidLink:
    def _evaluate(self, text, urlopen):
        evaluate = _load_evaluate("contains_valid_link.yaml")
        with mock.patch("urllib.request.urlopen", urlopen):
            return evaluate(None, text, None, None)

    def test_email_only_text_is_not_a_link(self):
        urlopen = mock.Mock(return_value=_response())
        result = self._evaluate("Contact us at support@acme.io for help", urlopen)
        assert result["score"] == 0.0
        urlopen.assert_not_called()

    def test_decimals_and_abbreviations_are_not_links(self):
        urlopen = mock.Mock(return_value=_response())
        result = self._evaluate("Pi is roughly 3.14, e.g. in rough estimates", urlopen)
        assert result["score"] == 0.0
        urlopen.assert_not_called()

    def test_scheme_url_is_detected_and_checked(self):
        urlopen = mock.Mock(return_value=_response())
        result = self._evaluate("See https://futureagi.com/docs for details", urlopen)
        assert result["score"] == 1.0
        requested = urlopen.call_args.args[0].full_url
        assert requested == "https://futureagi.com/docs"

    def test_bare_domain_is_still_detected(self):
        urlopen = mock.Mock(return_value=_response())
        result = self._evaluate("Visit futureagi.com to learn more", urlopen)
        assert result["score"] == 1.0
        requested = urlopen.call_args.args[0].full_url
        assert requested == "http://futureagi.com"

    def test_unreachable_link_fails(self):
        urlopen = mock.Mock(side_effect=OSError("connection refused"))
        result = self._evaluate("See https://dead.futureagi.com", urlopen)
        assert result["score"] == 0.0
        assert "unreachable" in result["reason"]


class TestNoInvalidLinks:
    def _evaluate(self, text, urlopen):
        evaluate = _load_evaluate("no_invalid_links.yaml")
        with mock.patch("urllib.request.urlopen", urlopen):
            return evaluate(None, text, None, None)

    def test_email_only_text_passes_without_network_calls(self):
        urlopen = mock.Mock(return_value=_response())
        result = self._evaluate("Contact us at support@acme.io for help", urlopen)
        assert result["score"] == 1.0
        urlopen.assert_not_called()

    def test_decimals_do_not_fail_linkless_text(self):
        urlopen = mock.Mock(side_effect=OSError("no such host"))
        result = self._evaluate("The value increased by 3.14 percent", urlopen)
        assert result["score"] == 1.0
        urlopen.assert_not_called()

    def test_dead_link_still_fails(self):
        urlopen = mock.Mock(side_effect=OSError("no such host"))
        result = self._evaluate("Read https://dead.futureagi.com/page", urlopen)
        assert result["score"] == 0.0
        assert "invalid links" in result["reason"].lower()

    def test_valid_links_pass(self):
        urlopen = mock.Mock(return_value=_response())
        result = self._evaluate(
            "Docs at https://futureagi.com/docs and www.futureagi.com", urlopen
        )
        assert result["score"] == 1.0
        assert urlopen.call_count == 2
