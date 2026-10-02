"""Offline SDK/protobuf contracts; never contact Temporal or initialize Django."""

import asyncio
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from temporalio.api.enums.v1 import IndexedValueType, NamespaceState
from temporalio.api.namespace.v1 import NamespaceInfo
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    AddSearchAttributesResponse,
    ListSearchAttributesRequest,
    ListSearchAttributesResponse,
)
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceRequest,
    DescribeNamespaceResponse,
)
from temporalio.service import RPCError, RPCStatusCode

from tracer.services.clickhouse import oss_peerdb_temporal as cli


def namespace(state=NamespaceState.NAMESPACE_STATE_REGISTERED, name="default"):
    return DescribeNamespaceResponse(
        namespace_info=NamespaceInfo(name=name, state=state)
    )


def attributes(value=cli.TEXT):
    return ListSearchAttributesResponse(
        custom_attributes={} if value is None else {"MirrorName": value}
    )


def client():
    return SimpleNamespace(
        workflow_service=SimpleNamespace(
            describe_namespace=AsyncMock(return_value=namespace())
        ),
        operator_service=SimpleNamespace(
            list_search_attributes=AsyncMock(return_value=attributes()),
            add_search_attributes=AsyncMock(return_value=AddSearchAttributesResponse()),
        ),
    )


def rpc_error(status):
    return RPCError("secret-password-from-server", status, b"")


class OfflineMixin:
    def guard_network(self):
        forbidden = Mock(
            side_effect=AssertionError("offline test attempted a connection")
        )
        # Connect guards leave asyncio's local socketpair usable. The native SDK
        # bridge is blocked too: its network sockets bypass Python's socket API.
        for target in (
            "socket.create_connection",
            "socket.getaddrinfo",
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "temporalio.bridge.client.Client.connect",
        ):
            patcher = patch(target, forbidden)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(forbidden.assert_not_called)


class TemporalSetupTests(OfflineMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guard_network()
        self.client = client()
        self.connect = AsyncMock(return_value=self.client)
        patcher = patch.object(cli.Client, "connect", self.connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(cli, "_POLL_INTERVAL", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_existing_text_is_read_only_even_with_apply(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.assertEqual(
                    await cli.run(apply=apply, env={}),
                    {"ready": True, "applied": False},
                )
        self.client.operator_service.add_search_attributes.assert_not_awaited()
        self.connect.assert_awaited_with(cli.ADDRESS, namespace="default", lazy=True)
        for rpc, request in (
            (
                self.client.workflow_service.describe_namespace,
                DescribeNamespaceRequest(namespace="default"),
            ),
            (
                self.client.operator_service.list_search_attributes,
                ListSearchAttributesRequest(namespace="default"),
            ),
        ):
            for call in rpc.await_args_list:
                self.assertEqual(call.args, (request,))
                self.assertIs(call.kwargs["retry"], False)
                self.assertGreater(call.kwargs["timeout"], timedelta(0))
                self.assertLessEqual(call.kwargs["timeout"], timedelta(seconds=10))

    async def test_missing_attribute_in_default_mode_is_not_ready(self):
        self.client.operator_service.list_search_attributes.return_value = attributes(
            None
        )
        self.assertEqual(await cli.run(env={}), {"ready": False, "applied": False})
        self.client.operator_service.add_search_attributes.assert_not_awaited()

    async def test_custom_address_and_namespace_flow_through_every_request(self):
        for address in (
            "temporal.internal:17233",
            "127.0.0.1:7233",
            "[::1]:7233",
            "temporal.internal.:7233",
        ):
            with self.subTest(address=address):
                fake = client()
                self.connect.return_value = fake
                fake.workflow_service.describe_namespace.return_value = namespace(
                    name="peerdb-customer"
                )
                fake.operator_service.list_search_attributes.side_effect = [
                    attributes(None),
                    attributes(),
                ]
                self.assertEqual(
                    await cli.run(
                        apply=True,
                        env={
                            "TEMPORAL_CLI_ADDRESS": address,
                            "PEERDB_TEMPORAL_NAMESPACE": "peerdb-customer",
                        },
                    ),
                    {"ready": True, "applied": True},
                )
                self.connect.assert_awaited_with(
                    address, namespace="peerdb-customer", lazy=True
                )
                for rpc in (
                    fake.workflow_service.describe_namespace,
                    fake.operator_service.list_search_attributes,
                    fake.operator_service.add_search_attributes,
                ):
                    for call in rpc.await_args_list:
                        self.assertEqual(call.args[0].namespace, "peerdb-customer")
                fake.operator_service.add_search_attributes.assert_awaited_once()

    async def test_custom_namespace_must_match_returned_namespace_exactly(self):
        with self.assertRaisesRegex(cli.TemporalSetupError, "REGISTERED"):
            await cli.run(
                apply=True, env={"PEERDB_TEMPORAL_NAMESPACE": "peerdb-customer"}
            )
        self.client.operator_service.add_search_attributes.assert_not_awaited()

    async def test_apply_once_then_wait_for_read_visibility(self):
        operator = self.client.operator_service
        operator.list_search_attributes.side_effect = [
            attributes(None),
            rpc_error(RPCStatusCode.UNAVAILABLE),
            attributes(None),
            attributes(None),
            attributes(),
        ]
        result = await cli.run(apply=True, env={})
        self.assertEqual(result, {"ready": True, "applied": True})
        operator.add_search_attributes.assert_awaited_once()
        call = operator.add_search_attributes.await_args
        self.assertEqual(
            call.args,
            (
                AddSearchAttributesRequest(
                    namespace="default", search_attributes={"MirrorName": cli.TEXT}
                ),
            ),
        )
        self.assertIs(call.kwargs["retry"], False)
        self.assertGreater(call.kwargs["timeout"], timedelta(0))
        self.assertLessEqual(call.kwargs["timeout"], timedelta(seconds=10))
        self.assertEqual(operator.list_search_attributes.await_count, 5)

    async def test_namespace_transients_only_retry_reads(self):
        self.client.workflow_service.describe_namespace.side_effect = [
            rpc_error(RPCStatusCode.UNAVAILABLE),
            rpc_error(RPCStatusCode.NOT_FOUND),
            namespace(),
        ]
        self.assertTrue((await cli.run(apply=True, env={}))["ready"])
        self.assertEqual(self.client.workflow_service.describe_namespace.await_count, 3)
        self.client.operator_service.add_search_attributes.assert_not_awaited()

    async def test_unregistered_or_wrong_namespace_is_fatal_before_attribute_access(
        self,
    ):
        for response in (
            namespace(0),
            namespace(2),
            namespace(3),
            namespace(name="other"),
        ):
            with self.subTest(response=response):
                self.client.workflow_service.describe_namespace.return_value = response
                with self.assertRaisesRegex(cli.TemporalSetupError, "REGISTERED"):
                    await cli.run(apply=True, env={})
        self.client.operator_service.list_search_attributes.assert_not_awaited()
        self.client.operator_service.add_search_attributes.assert_not_awaited()

    async def test_namespace_is_rechecked_immediately_before_write(self):
        self.client.operator_service.list_search_attributes.return_value = attributes(
            None
        )
        self.client.workflow_service.describe_namespace.side_effect = [
            namespace(),
            namespace(2),
        ]
        with self.assertRaisesRegex(cli.TemporalSetupError, "REGISTERED"):
            await cli.run(apply=True, env={})
        self.client.operator_service.add_search_attributes.assert_not_awaited()

    async def test_wrong_type_and_system_attribute_are_never_changed(self):
        for apply in (False, True):
            for response in (
                attributes(IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD),
                attributes(IndexedValueType.INDEXED_VALUE_TYPE_UNSPECIFIED),
                ListSearchAttributesResponse(
                    system_attributes={"MirrorName": cli.TEXT}
                ),
            ):
                with self.subTest(apply=apply, response=response):
                    self.client.operator_service.list_search_attributes.return_value = (
                        response
                    )
                    with self.assertRaises(cli.TemporalSetupError):
                        await cli.run(apply=apply, env={})
        self.client.operator_service.add_search_attributes.assert_not_awaited()

    async def test_fatal_read_statuses_are_not_retried_or_leaked(self):
        for status in RPCStatusCode:
            if status in (
                RPCStatusCode.OK,
                RPCStatusCode.UNAVAILABLE,
                RPCStatusCode.NOT_FOUND,
            ):
                continue
            for service, method in (
                ("workflow_service", "describe_namespace"),
                ("operator_service", "list_search_attributes"),
            ):
                with self.subTest(status=status, method=method):
                    fake = client()
                    self.connect.return_value = fake
                    rpc = getattr(getattr(fake, service), method)
                    rpc.side_effect = rpc_error(status)
                    with self.assertRaises(cli.TemporalSetupError) as caught:
                        await cli.run(apply=True, env={})
                    self.assertNotIn("secret-password", str(caught.exception))
                    rpc.assert_awaited_once()
                    fake.operator_service.add_search_attributes.assert_not_awaited()

    async def test_failed_write_is_never_retried_or_treated_as_success(self):
        for error in (
            rpc_error(RPCStatusCode.UNAVAILABLE),
            rpc_error(RPCStatusCode.NOT_FOUND),
            rpc_error(RPCStatusCode.ALREADY_EXISTS),
            RuntimeError("secret-password"),
        ):
            with self.subTest(error_type=type(error)):
                fake = client()
                self.connect.return_value = fake
                fake.operator_service.list_search_attributes.return_value = attributes(
                    None
                )
                fake.operator_service.add_search_attributes.side_effect = error
                with self.assertRaisesRegex(
                    cli.TemporalSetupError, "write outcome uncertain"
                ) as caught:
                    await cli.run(apply=True, env={})
                self.assertNotIn("secret-password", str(caught.exception))
                fake.operator_service.add_search_attributes.assert_awaited_once()
                fake.operator_service.list_search_attributes.assert_awaited_once()

    async def test_wrong_type_after_add_fails_without_another_write(self):
        operator = self.client.operator_service
        operator.list_search_attributes.side_effect = [attributes(None), attributes(2)]
        with self.assertRaisesRegex(cli.TemporalSetupError, "incompatible type"):
            await cli.run(apply=True, env={})
        operator.add_search_attributes.assert_awaited_once()

    async def test_hosted_and_replicated_apply_fail_before_connect(self):
        for env in (
            {"ENV_TYPE": "prod", "CLOUD_DEPLOYMENT": "US"},
            {"ENV_TYPE": " Production ", "CLOUD_DEPLOYMENT": " eu "},
            *(
                {"CH_USE_REPLICATED_ENGINES": value}
                for value in ("1", "true", "YES", " on ")
            ),
        ):
            with (
                self.subTest(env=env),
                self.assertRaisesRegex(cli.TemporalSetupError, "hosted/replicated"),
            ):
                await cli.run(apply=True, env=env)
        self.connect.assert_not_awaited()

    async def test_hosted_default_mode_still_checks_without_writes(self):
        self.assertTrue(
            (
                await cli.run(
                    env={
                        "ENV_TYPE": "production",
                        "CLOUD_DEPLOYMENT": "EU",
                        "CH_USE_REPLICATED_ENGINES": "true",
                    }
                )
            )["ready"]
        )
        self.client.operator_service.add_search_attributes.assert_not_awaited()

    async def test_hosted_guard_requires_both_production_and_cloud_region(self):
        for env in ({"ENV_TYPE": "production"}, {"CLOUD_DEPLOYMENT": "US"}):
            with self.subTest(env=env):
                fake = client()
                self.connect.return_value = fake
                fake.operator_service.list_search_attributes.side_effect = [
                    attributes(None),
                    attributes(),
                ]
                self.assertTrue((await cli.run(apply=True, env=env))["applied"])
                fake.operator_service.add_search_attributes.assert_awaited_once()

    async def test_invalid_target_namespace_or_timeout_fail_before_connect(self):
        cases = [
            *(
                {"env": {"TEMPORAL_CLI_ADDRESS": value}}
                for value in (
                    None,
                    True,
                    "",
                    "password@elsewhere:7233",
                    "http://temporal:7233",
                    "dns:///temporal:7233",
                    "temporal:7233/path",
                    "a:7233,b:7233",
                    "temporal",
                    "temporal:0",
                    "temporal:65536",
                    "temporal:-1",
                    "temporal:１２３",
                    "temporal:7233?password=x",
                    "temporal:7233\n",
                    "bad host:7233",
                    "-bad:7233",
                    "host..name:7233",
                    "::1:7233",
                    "[not-ip]:7233",
                    "999.1.1.1:7233",
                )
            ),
            *(
                {"env": {"PEERDB_TEMPORAL_NAMESPACE": value}}
                for value in (
                    None,
                    "",
                    " ",
                    "bad\x00name",
                    "bad\nname",
                    " name",
                    "x" * 256,
                )
            ),
            {"apply": "true"},
            *(
                {"timeout": value}
                for value in (0, -1, True, "300", float("inf"), float("nan"))
            ),
        ]
        for options in cases:
            with (
                self.subTest(options=options),
                self.assertRaises(cli.TemporalSetupError),
            ):
                await cli.run(**{"env": {}, **options})
        self.connect.assert_not_awaited()

    async def test_whole_deadline_bounds_connect_each_rpc_and_visibility(self):
        async def hang(*args, **kwargs):
            await asyncio.Event().wait()

        for phase in (
            "connect",
            "namespace",
            "list",
            "write",
            "visibility",
            "namespace-retries",
        ):
            with self.subTest(phase=phase):
                fake = client()
                self.connect.reset_mock(side_effect=True)
                self.connect.return_value = fake
                fake.operator_service.list_search_attributes.return_value = attributes(
                    None
                )
                if phase == "connect":
                    self.connect.side_effect = hang
                elif phase == "namespace":
                    fake.workflow_service.describe_namespace.side_effect = hang
                elif phase == "list":
                    fake.operator_service.list_search_attributes.side_effect = hang
                elif phase == "write":
                    fake.operator_service.add_search_attributes.side_effect = hang
                elif phase == "namespace-retries":
                    fake.workflow_service.describe_namespace.side_effect = rpc_error(
                        RPCStatusCode.NOT_FOUND
                    )
                with self.assertRaisesRegex(
                    cli.TemporalSetupError, "deadline exceeded"
                ):
                    await asyncio.wait_for(cli.run(apply=True, env={}, timeout=0.02), 1)
                self.assertEqual(
                    fake.operator_service.add_search_attributes.await_count,
                    int(phase in ("write", "visibility")),
                )
                for rpc in (
                    fake.workflow_service.describe_namespace,
                    fake.operator_service.list_search_attributes,
                    fake.operator_service.add_search_attributes,
                ):
                    for call in rpc.await_args_list:
                        self.assertIs(call.kwargs["retry"], False)
                        self.assertGreater(call.kwargs["timeout"], timedelta(0))
                        self.assertLessEqual(
                            call.kwargs["timeout"], timedelta(seconds=0.02)
                        )


class TemporalCliTests(OfflineMixin, unittest.TestCase):
    def setUp(self):
        self.guard_network()

    def test_cli_exit_codes_and_explicit_apply(self):
        for args, ready in (
            ([], True),
            ([], False),
            (["--apply", "--timeout", "12"], True),
        ):
            with self.subTest(args=args, ready=ready):
                result = {"ready": ready, "applied": "--apply" in args}
                with (
                    patch.object(cli, "run", AsyncMock(return_value=result)) as run,
                    redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(cli.main(args), 0 if ready else 1)
                self.assertEqual(json.loads(output.getvalue()), result)
                run.assert_awaited_once_with(
                    apply="--apply" in args, timeout=12 if args else 300
                )

    def test_cli_sanitizes_unexpected_errors_and_bad_arguments(self):
        for args in ([], ["--timeout", "secret-password"], ["--secret-password"]):
            with (
                self.subTest(args=args),
                patch.object(
                    cli, "run", AsyncMock(side_effect=RuntimeError("secret-password"))
                ),
                redirect_stderr(io.StringIO()) as output,
            ):
                self.assertEqual(cli.main(args), 1)
            self.assertNotIn("secret-password", output.getvalue())

    def test_module_import_does_not_load_django(self):
        # Fresh interpreter verifies transitive imports without relying on the
        # parent test process's module cache or any Django pytest configuration.
        code = """
import socket, sys
from unittest.mock import patch
with patch.object(socket.socket, 'connect', side_effect=AssertionError('network')):
    from tracer.services.clickhouse import oss_peerdb_temporal
assert not any(name == 'django' or name.startswith('django.') for name in sys.modules)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
