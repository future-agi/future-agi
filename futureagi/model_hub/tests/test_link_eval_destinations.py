"""Shipped link checks preserve extraction and validate formats entirely offline."""

import ast
import http.client
import os
import runpy
import socket
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).resolve().parents[3]
NAMES = ("contains_valid_link", "no_invalid_links")
HELPERS = ROOT / "futureagi/common/utils/link_format.py"


def code_for(name):
    path = ROOT / "futureagi/model_hub/system_evals/function" / (name + ".yaml")
    code = path.read_text().split("  code: |\n", 1)[1]
    return "\n".join(
        line[4:] if line.startswith("    ") else line for line in code.splitlines()
    )


def load(name):
    namespace = {}
    exec(code_for(name), namespace)  # noqa: S102
    return namespace["evaluate"]


class LinkAcceptance(unittest.TestCase):
    def setUp(self):
        # Assert calls as well as raising: a broad evaluator except must not hide
        # an attempted connection, including on a failing input or in a thread.
        # Load the HTTP libraries before patching their socket/connection classes,
        # so their import-time class definitions do not inherit from a Mock.
        for module, attribute in (
            (socket, "socket"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
            (socket, "gethostbyname"),
            (socket, "gethostbyname_ex"),
            (http.client, "HTTPConnection"),
            (http.client, "HTTPSConnection"),
            (urllib.request, "urlopen"),
            (requests.sessions.Session, "request"),
        ):
            patcher = mock.patch.object(
                module, attribute, side_effect=AssertionError("must stay offline")
            )
            called = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(called.assert_not_called)

    def assert_scores(self, text, scores):
        for name, score in zip(NAMES, scores, strict=True):
            with self.subTest(text=text, name=name):
                self.assertEqual(load(name)(None, text, None, None)["score"], score)

    def test_email_and_prose_are_not_links(self):
        for text in (
            "Contact support@acme.io",
            "Contact help@my-domain.example",
            "Contact first.last+tag@sub.acme.io",
            "The value increased by 3.14 percent",
            "e.g. a sample",
            "Version 1.2.3",
            "Read report.pdf and notes.txt",
            "",
        ):
            self.assert_scores(text, (0.0, 1.0))

    def test_valid_formats_need_no_configuration_or_reachability(self):
        for text in (
            "https://does-not-exist.example/docs",
            "HTTPS://EXAMPLE.COM./docs?x=1#top",
            "http://private.example/private",
            "http://metadata.google.internal/path",
            "https://example.com/redirect",
            "https://example.com/missing",
            "www.example.com",
            "example.com/docs",
            "http://11.0.0.1/path",
            "https://[2606:4700:4700::1111]:443/",
            "https://example.com:1/",
            "https://example.com:65535/",
            "https://example.com:5432/",
            "https://example.com:06379/",
            "https://example.com/a%20b",
            "https://bücher.example/",
            "https://xn--bcher-kva.example/",
            "https://example.xn--p1ai/",
            "https://example.com/" + "a" * 100_000,
            "https://" + ".".join(["a" * 63] * 3 + ["b" * 61]) + "/",
        ):
            self.assert_scores(text, (1.0, 1.0))

    def test_invalid_formats(self):
        for text in (
            "http://localhost/",
            "http://example.c/",
            "http://example.123/",
            "http://-bad.example/",
            "http://bad-.example/",
            "http://bad_host.example/",
            "http://bad..example/",
            "http://example.com../",
            "http://" + "a" * 64 + ".example/",
            "http://" + ".".join(["a" * 63] * 4) + "/",
            "http://example.xn--a/",
            "https:///missing-host",
            "http://user:private-marker@example.com/",
            "http://example.com:0/",
            "http://example.com:65536/",
            "http://example.com:abc/",
            "http://example.com:/",
            "http://example.com:-1/",
            "http://example.com:+80/",
            "http://example.com:80:90/",
            "http://[2606:4700:4700::1111]junk/",
            "http://[2606:4700:4700::1111/",
            "http://2606:4700:4700::1111/",
            "http://999.1.1.1/",
            "http://127.1/",
            "http://2130706433/",
            "http://example.com\\path",
            "http://example.com/a\x00b",
            "http://example.com/a\x7fb",
            "http://example.com/a{b}",
            "http://example.com/a|b",
            "http://example.com/a^b",
            "http://example.com/a`b",
            "http://example.com/%xy",
            "http://example.com/%1",
        ):
            self.assert_scores(text, (0.0, 0.0))

    def test_literal_addresses_must_be_public(self):
        for address in (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "192.0.2.1",
            "[::1]",
            "[fc00::1]",
            "[fe80::1]",
            "[fec0::1]",
            "[ff02::1]",
            "[2001:db8::1]",
            "[::ffff:11.0.0.1]",
            "[2002:0b00:1::1]",
            "[fe80::1%25eth0]",
        ):
            self.assert_scores(f"http://{address}/", (0.0, 0.0))

    def test_contains_checks_later_candidates_and_all_checks_every_candidate(self):
        for text in (
            "http://bad-.example/ https://example.com/",
            "https://example.com/ http://bad-.example/",
            "http://example.com:65536/ https://example.com/",
            "ftp://example.com/file https://example.com/",
            "https://example.com/ //example.com/path",
            "http://[broken/ https://example.com/",
        ):
            self.assert_scores(text, (1.0, 0.0))
        self.assert_scores("https://example.com/a http://other.example/b", (1.0, 1.0))

    def test_environment_and_kwargs_do_not_control_the_verdict(self):
        for setting in ("", "other.example", "*.example", "x" * 9000):
            with mock.patch.dict(os.environ, {"LINK_EVAL_ALLOWED_HOSTS": setting}):
                for name in NAMES:
                    result = load(name)(
                        None,
                        "http://example.com/",
                        None,
                        None,
                        allowed_hosts=[],
                        allow_private=True,
                        LINK_EVAL_ALLOWED_HOSTS="unused.example",
                    )
                    self.assertEqual(result["score"], 1.0)

    def test_text_mapping_takes_precedence_over_output(self):
        for name in NAMES:
            evaluate = load(name)
            self.assertEqual(
                evaluate(
                    None,
                    "http://bad-.example/",
                    None,
                    None,
                    text="https://example.com/",
                )["score"],
                1.0,
            )
            self.assertEqual(
                evaluate(
                    None,
                    "https://example.com/",
                    None,
                    None,
                    text="http://bad-.example/",
                )["score"],
                0.0,
            )

    def test_fixed_explanations_do_not_echo_input(self):
        for text, reasons in (
            (
                "https://private-marker.example/private-marker",
                ("Link found with valid format", "All 1 link(s) are valid"),
            ),
            (
                "https://private-marker-.example/private-marker",
                ("No link with valid format found", "Found links with invalid format"),
            ),
            (
                "ftp://private-marker.example/private-marker",
                (
                    "No valid link found (not http or https)",
                    "Found invalid links (not http or https)",
                ),
            ),
        ):
            for name, reason in zip(NAMES, reasons, strict=True):
                result = load(name)(None, text, None, None)
                self.assertEqual(result["reason"], reason)
                self.assertNotIn("private-marker", result["reason"])

    def test_helpers_match_legacy_and_both_self_contained_templates(self):
        def helpers(source):
            return [
                ast.dump(node)
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name != "evaluate"
            ]

        expected = helpers(HELPERS.read_text())
        for name in NAMES:
            self.assertEqual(helpers(code_for(name)), expected)

    def test_legacy_adapters_use_the_same_offline_semantics(self):
        # Isolate these two adapters from unrelated model/provider imports.
        path = ROOT / "futureagi/agentic_eval/core_evals/fi_evals/function/functions.py"
        tree = ast.parse(path.read_text())
        adapters = ast.Module(
            body=[
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in NAMES
            ],
            type_ignores=[],
        )
        namespace = runpy.run_path(str(HELPERS))
        exec(compile(adapters, str(path), "exec"), namespace)  # noqa: S102
        for text in (
            "https://example.com/",
            "http://bad-.example/ https://example.com/",
            "//example.com/path",
            "support@example.com",
        ):
            for name in NAMES:
                result = load(name)(None, text, None, None)
                self.assertEqual(
                    namespace[name](text),
                    {"result": bool(result["score"]), "reason": result["reason"]},
                )

    def test_twenty_http_links_are_allowed_but_twenty_one_fail(self):
        self.assert_scores(" ".join(f"example.com/{i}" for i in range(20)), (1.0, 1.0))
        for name in NAMES:
            result = load(name)(
                None, " ".join(f"example.com/{i}" for i in range(21)), None, None
            )
            self.assertEqual(
                result, {"score": 0.0, "reason": "Too many links to validate"}
            )
        self.assert_scores(
            "ftp://example.com/a " * 21 + "https://example.com/", (1.0, 0.0)
        )

    def test_system_eval_version_is_bumped(self):
        path = ROOT / "futureagi/model_hub/management/commands/seed_system_evals.py"
        versions = [
            node.value.value
            for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SYSTEM_EVALS_VERSION"
                for target in node.targets
            )
        ]
        self.assertEqual(len(versions), 1)
        self.assertGreater(versions[0], 32)

    def test_explicit_www_and_bare_links(self):
        for text, expected in (
            ("See https://allowed.example/docs", "https://allowed.example/docs"),
            ("Visit www.allowed.example.", "http://www.allowed.example"),
            ("Read allowed.example/help.", "http://allowed.example/help"),
            ("See (https://allowed.example/a_(b)).", "https://allowed.example/a_(b)"),
            ("HTTPS://allowed.example/docs", "HTTPS://allowed.example/docs"),
            ("support@acme.io then https://allowed.example", "https://allowed.example"),
            (
                "allowed.example/docs?email=support@acme.io",
                "http://allowed.example/docs?email=support@acme.io",
            ),
        ):
            for name in NAMES:
                evaluate = load(name)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(
                        evaluate.__globals__,
                        {"_valid_link": mock.Mock(return_value=True)},
                    ) as namespace,
                ):
                    self.assertEqual(evaluate(None, text, None, None)["score"], 1.0)
                    self.assertEqual(
                        namespace["_valid_link"].call_args.args[0], expected
                    )

    def test_other_scheme_and_scheme_relative_links_are_not_valid(self):
        for text in (
            "Download ftp://allowed.example/report.zip",
            "Connect to ws://allowed.example/socket",
            "Copy sftp://allowed.example/upload",
            "See //allowed.example/docs",
            "Open htp://allowed.example/docs",
            "Use file:///etc/hosts",
        ):
            for name in NAMES:
                evaluate = load(name)
                check = mock.Mock(return_value=True)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_valid_link": check}),
                    mock.patch("socket.getaddrinfo") as resolved,
                ):
                    result = evaluate(None, text, None, None)
                    self.assertEqual(result["score"], 0.0)
                    self.assertIn("not http or https", result["reason"])
                    check.assert_not_called()
                    resolved.assert_not_called()

    def test_other_scheme_link_beside_a_valid_https_link(self):
        text = "Mirror ftp://allowed.example/a.zip or https://allowed.example/a"
        expected = {"contains_valid_link": 1.0, "no_invalid_links": 0.0}
        for name in NAMES:
            evaluate = load(name)
            check = mock.Mock(return_value=True)
            with (
                self.subTest(name=name),
                mock.patch.dict(evaluate.__globals__, {"_valid_link": check}),
            ):
                self.assertEqual(
                    evaluate(None, text, None, None)["score"], expected[name]
                )
                if name == "contains_valid_link":
                    self.assertEqual(
                        check.call_args.args[0], "https://allowed.example/a"
                    )

    def test_double_slashes_in_prose_are_not_links(self):
        for text in (
            "a // comment",
            "path/to//file",
            "10 // 3",
        ):
            for name, score in zip(NAMES, (0.0, 1.0), strict=True):
                evaluate = load(name)
                check = mock.Mock(return_value=True)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_valid_link": check}),
                ):
                    result = evaluate(None, text, None, None)
                    self.assertEqual(result["score"], score)
                    check.assert_not_called()

    def test_code_comments_and_build_labels_are_not_links(self):
        for text in (
            "//TODO tidy this up",
            "//nolint:errcheck",
            "//go:build linux",
            "//pkg:target",
            "//foo/bar:baz",
            "x = 1; //increment",
            "f(); //console.log(x)",
            "//eslint-disable-next-line no-console",
            "f(); //console.log",
            "//package.json",
            "see //config.yaml:3",
        ):
            for name in NAMES:
                evaluate = load(name)
                check = mock.Mock(return_value=True)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_valid_link": check}),
                ):
                    self.assertEqual(evaluate.__globals__["_links"](text), [])
                    result = evaluate(None, text, None, None)
                    if name == "no_invalid_links":
                        self.assertEqual(result["score"], 1.0)
                    else:
                        self.assertEqual(result["score"], 0.0)
                        self.assertEqual(result["reason"], "No link found in output")
                    check.assert_not_called()

    def test_scheme_relative_link_forms_are_found(self):
        for text, link in (
            ("//allowed.example", "//allowed.example"),
            ("See //allowed.example/docs.", "//allowed.example/docs"),
            ('href="//allowed.example/x"', "//allowed.example/x"),
            ("(//allowed.example)", "//allowed.example"),
            ("[//allowed.example/x]", "//allowed.example/x"),
            ("<//allowed.example/x>", "//allowed.example/x"),
            ("Use //allowed.example:8080/x", "//allowed.example:8080/x"),
            ("Use //allowed.example?q=1", "//allowed.example?q=1"),
            ("Use //allowed.example#top", "//allowed.example#top"),
            ("Use //10.0.0.1/x", "//10.0.0.1/x"),
            ("Use //[2001:db8::1]/x", "//[2001:db8::1]/x"),
            ("Use //user@allowed.example/x", "//user@allowed.example/x"),
            ("Use `//allowed.example`", "//allowed.example"),
            ("See \u201c//allowed.example/x\u201d", "//allowed.example/x\u201d"),
            ("See \u2018//allowed.example\u2019", "//allowed.example"),
            ("Use //allowed.xn--p1ai/x", "//allowed.xn--p1ai/x"),
        ):
            for name in NAMES:
                evaluate = load(name)
                check = mock.Mock(return_value=True)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_valid_link": check}),
                ):
                    self.assertEqual(evaluate.__globals__["_links"](text), [link])
                    result = evaluate(None, text, None, None)
                    self.assertEqual(result["score"], 0.0)
                    self.assertIn("not http or https", result["reason"])
                    check.assert_not_called()

    def test_scheme_relative_link_after_equals_or_colon_is_found(self):
        for text, link in (
            ("<img src=//allowed.example/pixel>", "//allowed.example/pixel"),
            ("href=//allowed.example", "//allowed.example"),
            ("<a href=//allowed.example/x>", "//allowed.example/x"),
            ("url: //allowed.example/x", "//allowed.example/x"),
            ("Open http:://allowed.example/x", "//allowed.example/x"),
        ):
            for name in NAMES:
                evaluate = load(name)
                check = mock.Mock(return_value=True)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_valid_link": check}),
                ):
                    self.assertEqual(evaluate.__globals__["_links"](text), [link])
                    result = evaluate(None, text, None, None)
                    self.assertEqual(result["score"], 0.0)
                    self.assertIn("not http or https", result["reason"])
                    check.assert_not_called()

    def test_explicit_scheme_link_is_found_once(self):
        for text, links in (
            ("ftp://allowed.example/x", ["ftp://allowed.example/x"]),
            ("Connect to ws://allowed.example", ["ws://allowed.example"]),
            ("src=ftp://allowed.example/a", ["ftp://allowed.example/a"]),
            ("x:ftp://allowed.example", ["ftp://allowed.example"]),
            (
                "href=https://allowed.example/a",
                ["https://allowed.example/a"],
            ),
        ):
            for name in NAMES:
                with self.subTest(text=text, name=name):
                    self.assertEqual(load(name).__globals__["_links"](text), links)

    def test_comment_after_equals_is_not_a_link(self):
        for text in (
            "x=//increment",
            "flag=//go:build linux",
            "a=//TODO tidy",
            "path=//package.json",
        ):
            for name in NAMES:
                with self.subTest(text=text, name=name):
                    self.assertEqual(load(name).__globals__["_links"](text), [])

    def test_long_runs_of_closing_brackets_are_trimmed_quickly(self):
        for text, links in (
            ("ftp://allowed.example/" + ")" * 1_000_000, ["ftp://allowed.example/"]),
            (
                "https://allowed.example/" + ")" * 1_000_000,
                ["https://allowed.example/"],
            ),
            (" //allowed.example/" + ")" * 1_000_000, ["//allowed.example/"]),
            ("allowed.example/" + ")" * 1_000_000, ["http://allowed.example/"]),
            (
                "https://allowed.example/" + "]" * 1_000_000,
                ["https://allowed.example/"],
            ),
            (
                "https://allowed.example/a_(b" + ")" * 1_000_000,
                ["https://allowed.example/a_(b)"],
            ),
        ):
            for name in NAMES:
                links_of = load(name).__globals__["_links"]
                with self.subTest(name=name, text=text[:30]):
                    started = time.monotonic()
                    self.assertEqual(links_of(text), links)
                    self.assertLess(time.monotonic() - started, 10)

    def test_many_email_like_words_are_skipped_quickly(self):
        for text in (
            "a.bc@x " * 150_000,
            ",a.bc@x" * 150_000,
        ):
            for name in NAMES:
                links_of = load(name).__globals__["_links"]
                with self.subTest(name=name, text=text[:8]):
                    started = time.monotonic()
                    self.assertEqual(links_of(text), [])
                    self.assertLess(time.monotonic() - started, 10)

    def test_link_matching_stays_fast_on_long_input(self):
        inputs = (
            "a" * 1_000_000,
            "a+" * 500_000,
            "ftp:" * 250_000,
            "/" * 1_000_000,
            "a." * 500_000,
            "x://" * 250_000,
            " //a" * 250_000,
            " //" + "a." * 500_000,
            " //a.bc" + "." * 1_000_000 + "x",
            " //[" + ":" * 1_000_000,
            " //a@" * 250_000,
            " //a.b" * 200_000,
            " //" + "a" * 1_000_000,
        )
        for name in NAMES:
            links = load(name).__globals__["_links"]
            for text in inputs:
                with self.subTest(name=name, text=text[:8]):
                    started = time.monotonic()
                    try:
                        links(text)
                    except ValueError:
                        pass
                    self.assertLess(time.monotonic() - started, 10)

    def test_link_budget_refuses_before_any_request(self):
        for name in NAMES:
            evaluate = load(name)
            check = mock.Mock()
            with (
                self.subTest(name=name),
                mock.patch.dict(evaluate.__globals__, {"_valid_link": check}),
            ):
                result = evaluate(
                    None,
                    " ".join(f"allowed.example/{i}" for i in range(21)),
                    None,
                    None,
                )
                self.assertEqual(result["score"], 0.0)
                self.assertEqual(result["reason"], "Too many links to validate")
                check.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
