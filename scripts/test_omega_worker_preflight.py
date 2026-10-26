import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location(
    "preflight", Path(__file__).with_name("omega-worker-preflight.py")
)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def response(value):
    return io.BytesIO(json.dumps(value).encode())


def error(status):
    return HTTPError(preflight.REPOSITORY_URL, status, "registry failure", {}, None)


class WorkerPreflightTests(unittest.TestCase):
    def test_authenticated_missing_tag_in_private_repo_allows_release(self):
        with patch.object(
            preflight,
            "urlopen",
            side_effect=[
                response({"access_token": "test-token"}),
                response({"is_private": True}),
                error(404),
            ],
        ) as request:
            preflight.check_release("v1.2.3", "account", "credential")
        calls = request.call_args_list
        self.assertEqual(
            json.loads(calls[0].args[0].data),
            {
                "identifier": "account",
                "secret": "credential",
            },
        )
        for call in calls[1:]:
            self.assertEqual(
                call.args[0].get_header("Authorization"), "Bearer test-token"
            )
        self.assertEqual(
            calls[-1].args[0].full_url, preflight.REPOSITORY_URL + "/tags/v1.2.3"
        )

    def test_public_unknown_or_missing_repository_stops_release(self):
        for value in [False, None, "true"]:
            with (
                self.subTest(value=value),
                patch.object(
                    preflight,
                    "urlopen",
                    side_effect=[
                        response({"access_token": "test-token"}),
                        response({"is_private": value}),
                    ],
                ) as request,
            ):
                with self.assertRaises(RuntimeError):
                    preflight.check_release("v1.2.3", "account", "credential")
                self.assertEqual(request.call_count, 2)
        with (
            patch.object(
                preflight,
                "urlopen",
                side_effect=[
                    response({"access_token": "test-token"}),
                    error(404),
                ],
            ),
            self.assertRaises(HTTPError),
        ):
            preflight.check_release("v1.2.3", "account", "credential")

    def test_existing_tag_and_lookup_errors_never_allow_rebuild(self):
        for result in [
            response({"name": "v1.2.3"}),
            error(401),
            error(403),
            error(429),
            error(500),
            OSError("offline"),
        ]:
            with (
                self.subTest(result=result),
                patch.object(
                    preflight,
                    "urlopen",
                    side_effect=[
                        response({"access_token": "test-token"}),
                        response({"is_private": True}),
                        result,
                    ],
                ),
                self.assertRaises((RuntimeError, OSError)),
            ):
                preflight.check_release("v1.2.3", "account", "credential")

    def test_authentication_failure_is_not_absence(self):
        for result in [
            error(401),
            error(403),
            error(404),
            error(500),
            response({"access_token": ""}),
        ]:
            with (
                self.subTest(result=result),
                patch.object(preflight, "urlopen", side_effect=[result]) as request,
            ):
                with self.assertRaises((OSError, ValueError)):
                    preflight.check_release("v1.2.3", "account", "credential")
                self.assertEqual(request.call_count, 1)

    def test_invalid_version_and_missing_credentials_fail_before_network(self):
        for args in [
            ("latest", "account", "credential"),
            ("v1.2.3\nx=1", "account", "credential"),
            ("v1.2.3", "", "credential"),
            ("v1.2.3", "account", ""),
        ]:
            with self.subTest(args=args), patch.object(preflight, "urlopen") as request:
                with self.assertRaises(ValueError):
                    preflight.check_release(*args)
                request.assert_not_called()

    def test_release_retry_reports_existing_image_without_permitting_rebuild(self):
        for present in [True, False]:
            with (
                self.subTest(present=present),
                patch.object(
                    preflight,
                    "urlopen",
                    side_effect=[
                        response({"access_token": "test-token"}),
                        response({"is_private": True}),
                        response({"name": "v1.2.3"}) if present else error(404),
                    ],
                ),
            ):
                self.assertEqual(
                    preflight.check_release(
                        "v1.2.3", "account", "credential", allow_existing=True
                    ),
                    present,
                )


if __name__ == "__main__":
    unittest.main()
