"""Key attempts are limited per key and client address, so a flood never locks the owner out.

SB W2-KEYS round 3 (verifier minor finding M1): counted per key alone, wrong secrets from anyone
who holds only a key's public id spent the budget the owner's right secret needs. Now each attempt
counts against the key from the caller's address and against the key's ceiling across all
addresses (``accounts/omega_throttles.py``); a refused attempt counts against neither. The concurrent
proofs at the default numbers are in ``test_omega_rate_window.py``.
"""

import os
from unittest.mock import patch

from django.core.cache import cache

from accounts import omega_rate_window
from accounts.tests.personal_keys import personal_key_for
from accounts.tests.test_omega_session_exchange import ExchangeFixture

SOURCE_RATE = "OMEGA_SESSION_EXCHANGE_KEY_THROTTLE_RATE"
CEILING_RATE = "OMEGA_SESSION_EXCHANGE_KEY_CEILING_RATE"
TRUSTED = "OMEGA_SESSION_TRUSTED_PROXIES"


class OmegaSessionKeyLimitTests(ExchangeFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.key = personal_key_for(
            self.user, self.organization, workspace=self.workspace
        )

    def attempt(self, address, *, right=False, forwarded=None):
        headers = {
            "HTTP_X_API_KEY": self.key.api_key,
            "HTTP_X_SECRET_KEY": self.key.secret_key if right else "not-the-secret",
            "REMOTE_ADDR": address,
        }
        if forwarded is not None:
            headers["HTTP_X_FORWARDED_FOR"] = forwarded
        return self.exchange(headers=headers)

    def test_a_flood_from_one_address_never_locks_the_owner_out_from_another(self):
        with patch.dict(os.environ, {SOURCE_RATE: "3/min"}):
            flood = [self.attempt("198.51.100.66").status_code for _ in range(6)]
            same = self.attempt("198.51.100.66", right=True)
            owner = self.attempt("192.0.2.10", right=True)
        self.assertEqual(flood, [401] * 3 + [429] * 3)
        self.assertEqual(same.status_code, 429)
        self.assertGreaterEqual(int(same["Retry-After"]), 1)
        self.assertIn("from this address", same.data["message"])
        self.assertNotIn("accessToken", same.data)
        self.assertEqual(self.claims(owner)["key_id"], str(self.key.pk))

    def test_the_key_ceiling_bounds_guessing_from_many_addresses(self):
        with patch.dict(os.environ, {SOURCE_RATE: "2/min", CEILING_RATE: "5/min"}):
            spread = [
                self.attempt(f"203.0.113.{n}").status_code for n in (1, 1, 2, 2, 3)
            ]
            fresh = self.attempt("203.0.113.4", right=True)
        self.assertEqual(spread, [401] * 5)
        self.assertEqual(fresh.status_code, 429)
        self.assertNotIn("from this address", fresh.data["message"])
        self.assertGreaterEqual(int(fresh["Retry-After"]), 1)

    def test_a_refused_attempt_spends_nothing_of_the_key_ceiling(self):
        with patch.dict(os.environ, {SOURCE_RATE: "1/min", CEILING_RATE: "2/min"}):
            first = self.attempt("198.51.100.66").status_code
            refused = [self.attempt("198.51.100.66").status_code for _ in range(10)]
            owner = self.attempt("192.0.2.10", right=True)
        self.assertEqual((first, set(refused)), (401, {429}))
        self.assertEqual(owner.status_code, 200)

    def test_x_forwarded_for_names_the_source_only_from_a_trusted_proxy(self):
        gateway = "10.20.0.5"
        with patch.dict(os.environ, {SOURCE_RATE: "2/min"}):
            # Untrusted: a caller cannot pick a new address per attempt by writing the header.
            spoofed = [
                self.attempt(gateway, forwarded=f"198.51.100.{n}").status_code
                for n in range(1, 5)
            ]
            self.assertEqual(spoofed, [401, 401, 429, 429])
            cache.clear()
            with patch.dict(os.environ, {TRUSTED: "10.20.0.0/24"}):
                flood = [
                    self.attempt(gateway, forwarded="198.51.100.66").status_code
                    for _ in range(3)
                ]
                # A client-written hop left of the trusted proxy's is never believed.
                chained = self.attempt(
                    gateway, forwarded="192.0.2.99, 198.51.100.66", right=True
                )
                owner = self.attempt(gateway, forwarded="192.0.2.10", right=True)
        self.assertEqual(flood, [401, 401, 429])
        self.assertEqual(chained.status_code, 429)
        self.assertEqual(owner.status_code, 200)

    def test_a_limit_store_outage_refuses_and_mints_nothing(self):
        class Broken:
            def register_script(self, script):
                raise ConnectionError("redis is down")

        with patch.object(omega_rate_window, "_redis_client", return_value=Broken()):
            key = self.attempt("192.0.2.10", right=True)
            browser = self.exchange()
        for response in (key, browser):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response["Retry-After"], "2")
            self.assertNotIn("accessToken", response.data)
