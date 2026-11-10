"""Run seeded link-eval bodies offline; also discoverable by pytest."""

import unittest
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

YAML_DIR = Path(__file__).resolve().parent.parent / "function"
EVAL_NAMES = ("contains_valid_link", "no_invalid_links")


def _load_eval(name):
    path = YAML_DIR / f"{name}.yaml"
    # These templates end with a literal code block. Extract that exact body
    # without importing Django or requiring a YAML parser for the offline run.
    _, marker, body = path.read_text().partition("\n  code: |\n")
    if not marker:
        raise AssertionError(f"Missing literal code block in {path}")
    namespace = {}
    exec(compile(dedent(body), str(path), "exec"), namespace)
    return namespace["evaluate"]


class LinkEvalTests(unittest.TestCase):
    def test_link_free_text_never_makes_requests(self):
        texts = [
            "Contact us at support@acme.io for help",
            "Contact first.last@sub.acme.co.uk.",
            "Contact first.last+tag@acme.io.",
            "<mailto:support@www.acme.io>",
            "first.last@example.com and other@sub.example.org",
            "The value increased by 3.14 percent",
            "The value is -0.25 or 123.456.",
            "Use e.g. a sample, i.e. ordinary prose.",
            "Version v1.2.3 is ready",
            "Release 2.0.1 is ready",
            "Just ordinary text",
            "",
        ]
        for name in EVAL_NAMES:
            evaluate = _load_eval(name)
            for text in texts:
                for reachable in (True, False):
                    for use_text_kwarg in (True, False):
                        with self.subTest(
                            name=name,
                            text=text,
                            reachable=reachable,
                            use_text_kwarg=use_text_kwarg,
                        ), patch("urllib.request.urlopen") as urlopen:
                            urlopen.return_value = SimpleNamespace(status=200)
                            if not reachable:
                                urlopen.side_effect = URLError("offline")
                            if use_text_kwarg:
                                result = evaluate(
                                    None,
                                    "https://ignored.example.com",
                                    None,
                                    None,
                                    text=text,
                                )
                            else:
                                result = evaluate(None, text, None, None)

                            urlopen.assert_not_called()
                            self.assertEqual(
                                result["score"],
                                0.0 if name == "contains_valid_link" else 1.0,
                            )
                            self.assertIn("No link", result["reason"])

    def test_real_links_still_receive_head_requests(self):
        cases = [
            ("Visit https://example.com", "https://example.com"),
            ("Visit http://example.com", "http://example.com"),
            ("Visit HTTPS://EXAMPLE.COM", "HTTPS://EXAMPLE.COM"),
            ("Visit www.example.com", "http://www.example.com"),
            ("Visit futureagi.com", "http://futureagi.com"),
            ("Visit sub.example.co.uk", "http://sub.example.co.uk"),
            (
                "Visit my-site.example:8080/docs?q=1#top",
                "http://my-site.example:8080/docs?q=1#top",
            ),
            ("Visit https://192.0.2.1/docs", "https://192.0.2.1/docs"),
            ("Visit https://[2001:db8::1]/docs", "https://[2001:db8::1]/docs"),
            ("Visit [the docs](https://example.com/docs).", "https://example.com/docs"),
            ("Visit <https://example.com/docs>.", "https://example.com/docs"),
            ("Visit 'https://example.com/docs'.", "https://example.com/docs"),
            ("Visit example.com.", "http://example.com"),
            (
                "Visit [the docs](https://example.com/wiki/Function_(math)).",
                "https://example.com/wiki/Function_(math)",
            ),
            (
                "Visit https://example.com/?email=user@example.org",
                "https://example.com/?email=user@example.org",
            ),
            ("Visit https://example.com or email support@acme.io", "https://example.com"),
            ("Email first.last@acme.io or visit https://example.com", "https://example.com"),
            (
                "Version 3.14; see futureagi.com or email support@acme.io",
                "http://futureagi.com",
            ),
        ]
        for name in EVAL_NAMES:
            evaluate = _load_eval(name)
            for text, expected_url in cases:
                with self.subTest(name=name, text=text), patch(
                    "urllib.request.urlopen", return_value=SimpleNamespace(status=200)
                ) as urlopen:
                    result = evaluate(None, text, None, None)

                    self.assertEqual(result["score"], 1.0)
                    urlopen.assert_called_once()
                    request = urlopen.call_args.args[0]
                    self.assertEqual(request.full_url, expected_url)
                    self.assertEqual(request.get_method(), "HEAD")
                    self.assertEqual(urlopen.call_args.kwargs, {"timeout": 5})

    def test_real_unreachable_links_still_fail(self):
        for name in EVAL_NAMES:
            evaluate = _load_eval(name)
            for failure in (503, URLError("offline")):
                with self.subTest(name=name, failure=str(failure)), patch(
                    "urllib.request.urlopen"
                ) as urlopen:
                    if isinstance(failure, Exception):
                        urlopen.side_effect = failure
                    else:
                        urlopen.return_value = SimpleNamespace(status=failure)

                    result = evaluate(None, "https://example.com", None, None)

                    self.assertEqual(result["score"], 0.0)
                    self.assertIn("https://example.com", result["reason"])
                    urlopen.assert_called_once()

    def test_no_invalid_links_checks_every_real_link_but_not_prose(self):
        evaluate = _load_eval("no_invalid_links")
        with patch(
            "urllib.request.urlopen",
            side_effect=[SimpleNamespace(status=200), URLError("offline")],
        ) as urlopen:
            result = evaluate(
                None,
                "Contact support@acme.io; version 3.14. "
                "See https://example.com and www.example.org.",
                None,
                None,
            )

        self.assertEqual(result["score"], 0.0)
        self.assertIn("http://www.example.org (unreachable:", result["reason"])
        self.assertEqual(
            [call.args[0].full_url for call in urlopen.call_args_list],
            ["https://example.com", "http://www.example.org"],
        )
        self.assertTrue(
            all(call.args[0].get_method() == "HEAD" for call in urlopen.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
