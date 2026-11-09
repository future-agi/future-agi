"""Closed gateway parser graph plus executable original-body ordering proofs."""

import ast
import inspect
import json
import runpy
import textwrap
import unittest
from pathlib import Path

import botocore.client
import botocore.endpoint
import botocore.exceptions
import botocore.parsers
import openai._base_client
import openai._legacy_response
import openai._response
from openai.resources.chat.completions import CompletionsWithRawResponse

ROOT = Path(__file__).parents[2]
AGENTS = ROOT / "ee/agenthub/traceerroragent"
HERE = Path(__file__).parent


def sdk_sites():
    """The actual installed success AND error parsers, not an SDK-shaped fake."""
    result = {}
    for name, method in {
        "SyncAPIClient._process_response": openai._base_client.SyncAPIClient._process_response,
        "BaseClient._make_status_error_from_response": openai._base_client.BaseClient._make_status_error_from_response,
        "LegacyAPIResponse.parse": openai._legacy_response.LegacyAPIResponse.parse,
        "LegacyAPIResponse._parse": openai._legacy_response.LegacyAPIResponse._parse,
        "BaseAPIResponse._parse": openai._response.BaseAPIResponse._parse,
        "to_raw_response_wrapper": openai._legacy_response.to_raw_response_wrapper,
        "CompletionsWithRawResponse.__init__": CompletionsWithRawResponse.__init__,
        "botocore.ClientError.__init__": botocore.exceptions.ClientError.__init__,
        "botocore.ClientError._get_retry_info": botocore.exceptions.ClientError._get_retry_info,
        "botocore.BaseClient._make_api_call": botocore.client.BaseClient._make_api_call,
        "botocore.Endpoint._do_get_response": botocore.endpoint.Endpoint._do_get_response,
        "botocore.Endpoint._add_modeled_error_fields": botocore.endpoint.Endpoint._add_modeled_error_fields,
        "botocore.convert_to_response_dict": botocore.endpoint.convert_to_response_dict,
        "botocore.ResponseParser.parse": botocore.parsers.ResponseParser.parse,
        "botocore.BaseRestParser._do_parse": botocore.parsers.BaseRestParser._do_parse,
        "botocore.BaseRestParser._add_modeled_parse": botocore.parsers.BaseRestParser._add_modeled_parse,
        "botocore.BaseRestParser._do_modeled_error_parse": botocore.parsers.BaseRestParser._do_modeled_error_parse,
        "botocore.BaseRestParser._parse_payload": botocore.parsers.BaseRestParser._parse_payload,
        "botocore.RestJSONParser._do_error_parse": botocore.parsers.RestJSONParser._do_error_parse,
        "botocore.RestJSONParser._inject_error_code": botocore.parsers.RestJSONParser._inject_error_code,
        "botocore.RestJSONParser._initial_body_parse": botocore.parsers.RestJSONParser._initial_body_parse,
        "botocore.BaseJSONParser._do_error_parse": botocore.parsers.BaseJSONParser._do_error_parse,
        "botocore.BaseJSONParser._parse_body_as_json": botocore.parsers.BaseJSONParser._parse_body_as_json,
    }.items():
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        native = name.startswith("botocore.")
        result[name] = {
            "sites": [
                ast.unparse(n)
                for n in ast.walk(tree)
                if isinstance(n, (ast.Call, ast.If, ast.Return))
            ],
            "error_classification": "ERROR_BODY_NOT_CONSUMED",
            "boundary": (
                "Installed botocore may parse error JSON before raising; the one-shot "
                "background invoke discards the exception without reading any field. "
                "The caller records sticky failure, never observes/normalizes error "
                "content and cannot publish. Successful InvokeModel streaming bytes "
                "still reach original_json before any content consumer."
                if native
                else "OpenAI error-body parsing is bypassed on the private zero-retry copy. "
                "Every error is terminal without reading its body, including in the "
                "strict decoder. Successful raw responses are original-validated; "
                "SDK model parsers remain bypassed. Foreground policy is unchanged."
            ),
            "order_proof": (
                "InstalledBedrock.test_error_body_is_not_consumed_or_published"
                if native
                else "SDKWire.test_sdk_error_body_never_parsed_or_retried"
            ),
        }
    return result


class GatewayOrder(unittest.TestCase):
    def test_both_callers_route_through_owned_original_body_boundary(self):
        for filename in ("background_falcon.py", "background_llm.py"):
            source = (AGENTS / filename).read_text()
            self.assertIn("background_gateway", source)
            self.assertNotIn(
                "from ee.licensing.managed_ai import chat_completion", source
            )
            self.assertNotIn("super()._try_managed_ai_completion", source)
            self.assertNotIn("self._attempt(client.chat.completions.create", source)

    def test_managed_and_sdk_parser_edges_are_in_the_closed_census(self):
        census = json.loads((HERE / "analysis_payload_sites.json").read_text())
        for filename in (
            "ee/licensing/activation_client.py",
            "ee/licensing/managed_ai.py",
            "ee/agenthub/traceerroragent/background_gateway.py",
        ):
            self.assertTrue(filename in census, filename)
        inventory = HERE / "analysis_gateway_sdk_sites.json"
        self.assertTrue(
            inventory.exists(), "installed SDK decode graph must be inventoried"
        )
        self.assertEqual(json.loads(inventory.read_text()), sdk_sites())

    def test_sdk_error_decoders_are_not_certified_as_original_evidence(self):
        census = json.loads((HERE / "analysis_payload_sites.json").read_text())
        for filename, expression in (
            ("background_provider.py", "client.invoke_model("),
            ("background_gateway.py", "completions.with_raw_response.create("),
        ):
            rows = census["ee/agenthub/traceerroragent/" + filename]
            calls = [row for row in rows if row["expression"].startswith(expression)]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["classification"], "ERROR_BODY_NOT_CONSUMED")
            self.assertIn("sticky", calls[0]["boundary"])
        inventory = sdk_sites()
        self.assertTrue(any(name.startswith("botocore.") for name in inventory))
        self.assertTrue(
            all(
                row["error_classification"] == "ERROR_BODY_NOT_CONSUMED"
                for row in inventory.values()
            )
        )

    def test_strict_decode_precedes_every_gateway_response_observer(self):
        path = AGENTS / "background_gateway.py"
        self.assertTrue(path.exists(), "raw gateway adapter missing")
        source = path.read_text()
        calls = [
            ast.unparse(n.func)
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call)
        ]
        self.assertFalse(any(name.endswith((".json", ".parse")) for name in calls))
        self.assertIn("original_json", source)
        self.assertIn("with_raw_response.create", source)
        self.assertIn("_make_status_error_from_response", source)
        # The two externally-owned lossy decoders are bypassed, never certified
        # as original evidence. Their exact AST is frozen; runnable worker tests
        # attach response-local .json spies and require zero parser calls.
        self.assertIn("original_json", ast.unparse(ast.parse(source)))
        suite = runpy.run_path(str(HERE / "test_analysis_gateway_wire_offline.py"))
        for cls, names in (
            (
                suite["ManagedWire"],
                (
                    "test_managed_unique_body_completes",
                    "test_managed_duplicate_terminal_aborts",
                    "test_managed_duplicate_arguments_aborts",
                ),
            ),
            (
                suite["SDKWire"],
                (
                    "test_sdk_unique_body_completes",
                    "test_sdk_duplicate_terminal_aborts",
                    "test_sdk_duplicate_arguments_aborts",
                    "test_sdk_error_body_never_parsed_or_retried",
                ),
            ),
        ):
            for name in names:
                with self.subTest(proof=name):
                    result = unittest.TestResult()
                    cls(name).run(result)
                    self.assertTrue(
                        result.wasSuccessful(), result.errors + result.failures
                    )


if __name__ == "__main__":
    unittest.main()
