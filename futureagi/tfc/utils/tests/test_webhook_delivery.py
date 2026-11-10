"""Public-destination and delivery contracts without any external HTTP calls."""

import json
import socket
import unittest
from unittest.mock import MagicMock, patch

from tfc.utils.ssrf_guard import SsrfBlocked
from tfc.utils.webhook_delivery import post_webhook, validate_webhook_url


def address_info(*addresses):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 0),
        )
        for address in addresses
    ]


class WebhookDestinationTests(unittest.TestCase):
    def test_rejects_nonpublic_addresses_before_connecting(self):
        for address in (
            "127.0.0.1",
            "10.1.2.3",
            "172.16.1.1",
            "192.168.1.1",
            "169.254.169.254",
            "100.100.100.200",
            "168.63.129.16",
            "0.0.0.0",
            "224.0.0.1",
            "100.64.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "::ffff:127.0.0.1",
            "fd00:ec2::254",
        ):
            with (
                self.subTest(address=address),
                patch(
                    "tfc.utils.ssrf_guard.socket.getaddrinfo",
                    return_value=address_info(address),
                ),
                patch("tfc.utils.webhook_delivery._open_pinned_pool") as pool,
            ):
                with self.assertRaises(SsrfBlocked):
                    post_webhook(
                        "http://receiver.invalid/event",
                        json={"event": "synthetic"},
                        headers={},
                        timeout=10,
                    )
                pool.assert_not_called()

    def test_rejects_mixed_public_private_dns_answer(self):
        with patch(
            "tfc.utils.ssrf_guard.socket.getaddrinfo",
            return_value=address_info("93.184.216.34", "127.0.0.1"),
        ):
            with self.assertRaises(SsrfBlocked):
                validate_webhook_url("https://receiver.invalid/hook")

    def test_rejects_invalid_url_without_dns(self):
        for url in (
            "file:///etc/hosts",
            "ftp://receiver.invalid/x",
            "https:///x",
            "https://user:password@receiver.invalid/x",
            "https://receiver.invalid/x#secret",
            "https://receiver.invalid:bad/x",
            "https://receiver.invalid:0/x",
            "https://receiver.invalid:65536/x",
            "https://receiver.invalid\\@127.0.0.1/x",
            "https://receiver.invalid/\nsecret",
        ):
            with (
                self.subTest(url=url),
                patch("tfc.utils.ssrf_guard.socket.getaddrinfo") as dns,
            ):
                with self.assertRaises(SsrfBlocked):
                    validate_webhook_url(url)
                dns.assert_not_called()

    def test_failure_message_does_not_contain_credentials_or_dns_diagnostics(self):
        with patch(
            "tfc.utils.ssrf_guard.socket.getaddrinfo",
            side_effect=socket.gaierror("secret-detail"),
        ):
            with self.assertRaises(SsrfBlocked) as caught:
                validate_webhook_url("https://receiver.invalid/hook?token=secret-token")
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("receiver", str(caught.exception))

    def test_dns_is_pinned_once_and_headers_keep_original_authority(self):
        response = MagicMock(status=204)
        pool = MagicMock()
        pool.request.return_value = response
        with (
            patch(
                "tfc.utils.ssrf_guard.socket.getaddrinfo",
                side_effect=[address_info("93.184.216.34"), address_info("127.0.0.1")],
            ) as dns,
            patch(
                "tfc.utils.webhook_delivery._open_pinned_pool", return_value=pool
            ) as open_pool,
        ):
            post_webhook(
                "https://receiver.invalid:8443/hook?token=synthetic",
                json={"value": 4},
                headers={"Content-Type": "application/json", "Host": "wrong.invalid"},
                timeout=10,
            )
        dns.assert_called_once_with("receiver.invalid", None)
        open_pool.assert_called_once_with(
            "https", "93.184.216.34", "receiver.invalid", 8443, 10
        )
        args, kwargs = pool.request.call_args
        self.assertEqual(args, ("POST", "/hook?token=synthetic"))
        self.assertEqual(json.loads(kwargs["body"]), {"value": 4})
        self.assertEqual(kwargs["headers"]["Host"], "receiver.invalid:8443")
        self.assertFalse(kwargs["redirect"])
        self.assertFalse(kwargs["retries"])
        self.assertFalse(kwargs["preload_content"])
        response.close.assert_called_once()
        pool.close.assert_called_once()

    def test_validation_does_not_authorize_a_later_private_resolution(self):
        with (
            patch(
                "tfc.utils.ssrf_guard.socket.getaddrinfo",
                side_effect=[address_info("93.184.216.34"), address_info("10.0.0.1")],
            ),
            patch("tfc.utils.webhook_delivery._open_pinned_pool") as pool,
        ):
            validate_webhook_url("https://receiver.invalid/hook")
            with self.assertRaises(SsrfBlocked):
                post_webhook(
                    "https://receiver.invalid/hook", json={}, headers={}, timeout=10
                )
            pool.assert_not_called()

    def test_redirects_and_non_success_are_refused_without_second_dispatch(self):
        for status in (301, 302, 303, 307, 308, 400, 500):
            with (
                self.subTest(status=status),
                patch(
                    "tfc.utils.ssrf_guard.socket.getaddrinfo",
                    return_value=address_info("93.184.216.34"),
                ),
                patch("tfc.utils.webhook_delivery._open_pinned_pool") as open_pool,
            ):
                pool = open_pool.return_value
                response = pool.request.return_value
                response.status = status
                response.headers = {"Location": "http://127.0.0.1/private"}
                with self.assertRaises(ValueError):
                    post_webhook(
                        "https://receiver.invalid/hook", json={}, headers={}, timeout=10
                    )
                pool.request.assert_called_once()
                open_pool.assert_called_once()
                response.close.assert_called_once()
                pool.close.assert_called_once()

    def test_connection_failure_closes_pool(self):
        with (
            patch(
                "tfc.utils.ssrf_guard.socket.getaddrinfo",
                return_value=address_info("93.184.216.34"),
            ),
            patch("tfc.utils.webhook_delivery._open_pinned_pool") as open_pool,
        ):
            pool = open_pool.return_value
            pool.request.side_effect = TimeoutError("synthetic timeout")
            with self.assertRaises(TimeoutError):
                post_webhook(
                    "https://receiver.invalid/hook", json={}, headers={}, timeout=10
                )
            pool.close.assert_called_once()
