"""Daemon-free adversarial cache/HTTP contracts; no optional dependencies."""
import base64
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from deploy.update import httpcache as hc
from deploy.update.contracts import ReasonCode as R
from deploy.update.trust import HttpResponse, ManifestError

URL = "https://example.test/releases/v1.2.3/manifest.json"
NOW = 1792800000.0


class Transport:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer()
        return replace(answer, final_url=answer.final_url or url)


class CacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.now = NOW
        self.mono = 10.0
        self.transport = Transport()
        self.cache = hc.HttpCache(self.tmp / "cache", transport=self.transport,
                                  clock=lambda: self.now, monotonic=lambda: self.mono)
        self.key = self.cache.key(URL, channel="stable")

    def fail(self, reason, function=None, *args, **kwargs):
        # Preserve unittest's own failure entry point.
        if function is None:
            return super().fail(reason)
        with self.assertRaises(hc.DiscoveryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason)
        self.assertEqual(str(caught.exception), reason.value)
        return caught.exception

    def seed(self, *, etag='"a"', last_modified="Wed, 21 Oct 2026 07:28:00 GMT", body=b"first"):
        self.transport.answers.append(HttpResponse(200, body, etag, last_modified))
        return self.cache.fetch(URL)

    def record(self):
        return self.cache.store.read("http-" + self.key)

    def change(self, **kwargs):
        value = self.record()
        value.update(kwargs)
        self.cache.store.write("http-" + self.key, value)

    def test_transport_reads_only_proof_bound_plus_one_and_never_disables_tls(self):
        from deploy.update.trust import _download, http_get
        import ssl
        reads = []
        class Response:
            headers = {}
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def geturl(self):
                return URL
            def read(self, maximum):
                reads.append(maximum)
                return b"x" * maximum
        with self.assertRaises(ManifestError):
            _download(URL, opener=lambda *a, **k: Response(), max_bytes=256 * 1024)
        self.assertEqual(reads, [256 * 1024 + 1])
        insecure = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        insecure.check_hostname = False
        insecure.verify_mode = ssl.CERT_NONE
        with self.assertRaises(ManifestError):
            http_get(URL, {}, ssl_context=insecure)

    def test_proof_bound_applies_to_transport_and_304_cache_body(self):
        self.seed(body=b"x" * (256 * 1024 + 1))
        def bounded(url, headers, timeout, *, max_bytes):
            self.assertEqual(max_bytes, 256 * 1024)
            return HttpResponse(304, etag='"a"', final_url=url)
        self.cache.transport = bounded
        self.fail(R.CACHE_INVALID, self.cache.fetch, URL, max_bytes=256 * 1024)
        def oversized(url, headers, timeout, *, max_bytes):
            return HttpResponse(200, b"x" * (max_bytes + 1), final_url=url)
        self.cache.transport = oversized
        self.fail(R.CACHE_INVALID, self.cache.fetch, URL, max_bytes=256 * 1024)

    def test_create_only_candidate_proof_rejects_corrupt_immutable_record(self):
        args = dict(channel="stable", source_identity="host-source", manifest=b"signed-manifest", bundle=b"bundle",
                    proof=b"proof", proof_sha256=hc.digest(b"proof"))
        name = self.cache.accept_evidence(URL, **args)
        self.assertEqual(self.cache.accept_evidence(URL, **args), name)
        record = self.cache.store.read(name, strict=True)
        record["body"] = base64.b64encode(b"changed").decode()
        self.cache.store.write(name, record)
        self.fail(R.IMMUTABLE_RELEASE_CHANGED, self.cache.accept_evidence, URL, **args)
        with self.assertRaises(hc.DiscoveryError):
            self.cache.accept_evidence(URL, **dict(args, proof=b"replay-other-target"))

    def test_200_304_reuses_exact_bytes_and_both_validators(self):
        first = self.seed()
        self.now += 15
        self.transport.answers.append(HttpResponse(304, etag='"a"'))
        second = self.cache.fetch(URL)
        self.assertEqual(second.body, first.body)
        self.assertTrue(second.not_modified)
        self.assertEqual(second.checked_at, self.now)
        self.assertEqual(self.transport.calls[-1][1], {"Accept": "application/json", "If-None-Match": '"a"',
                                                      "If-Modified-Since": "Wed, 21 Oct 2026 07:28:00 GMT"})
        self.assertEqual(self.transport.calls[-1][2], 30)
        self.assertEqual(self.record()["checked_at"], self.now)

    def test_changed_200_with_same_etag_is_new_untrusted_bytes(self):
        self.seed()
        self.transport.answers.append(HttpResponse(200, b"changed", '"a"'))
        self.assertEqual(self.cache.fetch(URL).body, b"changed")
        self.assertEqual(self.record()["sha256"], hc.digest(b"changed"))

    def test_missing_validators_never_sends_conditional_request(self):
        self.seed(etag=None, last_modified=None)
        self.transport.answers.append(HttpResponse(200, b"second"))
        self.assertEqual(self.cache.fetch(URL).body, b"second")
        self.assertEqual(self.transport.calls[-1][1], {"Accept": "application/json"})

    def test_last_modified_only_and_etag_only(self):
        for etag, last in ((None, "Wed, 21 Oct 2026 07:28:00 GMT"), ('"x"', None)):
            self.seed(etag=etag, last_modified=last)
            self.transport.answers.append(HttpResponse(304))
            self.assertTrue(self.cache.fetch(URL).not_modified)
            self.assertEqual(len(self.transport.calls[-1][1]), 2)

    def test_cold_304_retries_exactly_once_unconditionally(self):
        self.transport.answers += [HttpResponse(304), HttpResponse(200, b"fresh")]
        self.assertEqual(self.cache.fetch(URL).body, b"fresh")
        self.assertEqual(len(self.transport.calls), 2)
        self.assertTrue(all(call[1] == {"Accept": "application/json"} for call in self.transport.calls))

    def test_repeated_cold_304_is_cache_invalid(self):
        self.transport.answers += [HttpResponse(304), HttpResponse(304)]
        self.fail(R.CACHE_INVALID, self.cache.fetch, URL)
        self.assertEqual(len(self.transport.calls), 2)

    def test_mismatching_304_validators_or_body_force_unconditional_recovery(self):
        for answer in (HttpResponse(304, etag='"other"'), HttpResponse(304, last_modified="other"),
                       HttpResponse(304, b"illegal")):
            self.seed()
            self.transport.answers += [answer, HttpResponse(200, b"recovered")]
            self.assertEqual(self.cache.fetch(URL).body, b"recovered")
            self.assertEqual(self.transport.calls[-1][1], {"Accept": "application/json"})

    def test_corrupt_http_record_refetches_not_stale(self):
        cases = ({"body": "%%%"}, {"body": "eA==", "sha256": "bad"}, {"key": "another"},
                 {"etag": "bad\nheader"}, {"last_modified": "x" * 1025}, {"link": 12},
                 {"checked_at": "no"}, {"body": 2})
        for change in cases:
            with self.subTest(change=change):
                self.seed()
                self.change(**change)
                self.transport.answers.append(HttpResponse(200, b"recovered"))
                self.assertEqual(self.cache.fetch(URL).body, b"recovered")
                self.assertEqual(self.transport.calls[-1][1], {"Accept": "application/json"})
        self.seed()
        path = self.cache.store.directory / ("http-" + self.key + ".json")
        for raw in (b"{", b"[]", b'{"schema":2}', b"[" * 2000):
            path.write_bytes(raw)
            self.transport.answers.append(HttpResponse(200, b"recovered"))
            self.assertEqual(self.cache.fetch(URL).body, b"recovered")
        self.seed()
        value = self.record()
        del value["body"]
        self.cache.store.write("http-" + self.key, value)
        self.transport.answers += [HttpResponse(304), HttpResponse(200, b"recovered")]
        self.assertEqual(self.cache.fetch(URL).body, b"recovered")

    def test_unsafe_urls_and_identity_key_isolation(self):
        for url in ("http://host/file", "https://user:secret@host/file", "https://host/file#frag", "https://host/\n"):
            self.fail(R.MANIFEST_UNAVAILABLE, self.cache.fetch, url)
        self.assertEqual(self.transport.calls, [])
        base = self.cache.key(URL, channel="stable", source_identity="token-secret")
        for kwargs in ({"channel": "beta", "source_identity": "token-secret"},
                       {"channel": "stable", "source_identity": "other"},
                       {"channel": "stable", "source_identity": "token-secret", "representation": "other"}):
            self.assertNotEqual(base, self.cache.key(URL, **kwargs))
        self.assertNotEqual(base, self.cache.key(URL.replace("v1.2.3", "v1.2.4"), channel="stable"))
        self.assertEqual(self.key, self.cache.key(URL.replace("example.test", "EXAMPLE.TEST:443"), channel="stable"))
        self.fail(R.CHANNEL_MISMATCH, self.cache.fetch, URL, channel="nightly")

    def test_no_source_identity_or_query_in_metadata(self):
        self.transport.answers.append(HttpResponse(200, b"public"))
        self.cache.fetch(URL + "?token=QUERY-CANARY", source_identity="AUTH-CANARY")
        raw = b"".join(p.read_bytes() for p in self.cache.store.directory.iterdir())
        for secret in (b"QUERY-CANARY", b"AUTH-CANARY", URL.encode()):
            self.assertNotIn(secret, raw)
        self.assertEqual(self.transport.calls[-1][1], {"Accept": "application/json"})

    def test_invalid_headers_body_and_final_url_are_sanitized(self):
        for answer, reason in ((HttpResponse(200, etag="x\r\nsecret"), R.CACHE_INVALID),
                               (HttpResponse(200, last_modified="x" * 1025), R.CACHE_INVALID),
                               (HttpResponse(200, etag=""), R.CACHE_INVALID),
                               (HttpResponse(200, link="x\nsecret"), R.CACHE_INVALID),
                               (HttpResponse(200, b"x" * (hc.MAX_DOWNLOAD_BYTES + 1)), R.CACHE_INVALID),
                               (HttpResponse(200, "text"), R.CACHE_INVALID),
                               (HttpResponse(200, final_url="http://host/secret"), R.NETWORK_UNAVAILABLE),
                               (HttpResponse(200, final_url="https://u:secret@host/x"), R.NETWORK_UNAVAILABLE)):
            self.transport.answers.append(answer)
            self.fail(reason, self.cache.fetch, URL)
        with mock.patch.object(self.cache, "transport", return_value=None):
            self.fail(R.CACHE_INVALID, self.cache.fetch, URL)

    def test_network_failure_never_reuses_warm_cache(self):
        self.seed()
        for error in (OSError("CANARY"), ManifestError("CANARY"), ValueError("CANARY")):
            self.transport.answers.append(error)
            caught = self.fail(R.NETWORK_UNAVAILABLE, self.cache.fetch, URL)
            self.assertNotIn("CANARY", str(caught))
        for status, reason in ((404, R.RELEASE_INCOMPLETE), (410, R.RELEASE_INCOMPLETE),
                               (500, R.NETWORK_UNAVAILABLE), (403, R.NETWORK_UNAVAILABLE), (302, R.NETWORK_UNAVAILABLE)):
            self.transport.answers.append(HttpResponse(status))
            self.fail(reason, self.cache.fetch, URL)

    def test_rate_limit_is_persistent_and_blocks_network_until_due(self):
        self.transport.answers.append(HttpResponse(429, retry_after="120"))
        caught = self.fail(R.RATE_LIMITED, self.cache.fetch, URL)
        self.assertEqual(caught.retry_at, NOW + 120)
        self.now += 119
        self.fail(R.RATE_LIMITED, self.cache.fetch, URL)
        self.assertEqual(len(self.transport.calls), 1)
        self.now += 1
        self.seed()
        self.assertEqual(len(self.transport.calls), 2)

    def test_retry_after_defaults_dates_bounds_and_malformed(self):
        self.assertEqual(hc.retry_time(None, NOW), NOW + 60)
        self.assertEqual(hc.retry_time("9999999999", NOW), NOW + 86400)
        self.assertEqual(hc.retry_time("0", NOW), NOW)
        self.assertEqual(hc.retry_time("Wed, 21 Oct 2026 07:28:00 GMT", NOW), NOW)
        self.assertEqual(hc.retry_time("Wed, 21 Oct 2037 07:28:00 GMT", NOW), NOW + 86400)
        for value in ("yesterday", "-1", "Wed, 21 Oct 2026 07:28:00", "1.5", "9" * 30):
            self.fail(R.CACHE_INVALID, hc.retry_time, value, NOW)
        self.transport.answers.append(HttpResponse(403, retry_after="60"))
        self.fail(R.RATE_LIMITED, self.cache.fetch, URL)

    def test_clock_rollback_and_invalid_clocks_fail_closed(self):
        self.seed()
        self.now -= 1
        self.fail(R.CLOCK_SKEW, self.cache.fetch, URL)
        self.assertEqual(len(self.transport.calls), 1)
        for value in (float("nan"), float("inf"), -1, True, "123"):
            self.now = value
            self.fail(R.CLOCK_SKEW, self.cache.fetch, URL)
        self.now = NOW

        def rewind():
            self.now -= 1
            return HttpResponse(200, b"x")

        self.transport.answers.append(rewind)
        self.fail(R.CLOCK_SKEW, self.cache.fetch, URL)

    def test_bad_rate_record_is_not_ignored(self):
        for value, reason in (({"observed_at": NOW + 1, "retry_at": NOW + 10}, R.CLOCK_SKEW),
                               ({"observed_at": NOW, "retry_at": NOW + 999999}, R.CLOCK_SKEW),
                               ({"retry_at": NOW}, R.CACHE_INVALID)):
            self.cache.store.write("rate-" + self.key, value)
            self.fail(reason, self.cache.fetch, URL)
        self.assertEqual(self.transport.calls, [])

    def test_deadline_caps_request_and_rejects_late_result(self):
        self.transport.answers.append(HttpResponse(200, b"x"))
        self.cache.fetch(URL, deadline=15)
        self.assertEqual(self.transport.calls[-1][2], 5)
        self.fail(R.DISCOVERY_INCOMPLETE, self.cache.fetch, URL, deadline=10)

        def late():
            self.mono = 20
            return HttpResponse(200, b"x")

        self.transport.answers.append(late)
        self.fail(R.DISCOVERY_INCOMPLETE, self.cache.fetch, URL, deadline=15)

    def pair(self, **changes):
        kwargs = dict(channel="stable", source_identity="", manifest=b"manifest", bundle=b"bundle",
                      manifest_hash="a" * 64, trust_fingerprint="b" * 64)
        kwargs.update(changes)
        return self.cache.accept_pair(URL, **kwargs)

    def test_immutable_pair_retains_old_bytes_even_under_new_trust(self):
        self.pair()
        path = next(self.cache.store.directory.glob("pair-*.json"))
        old = path.read_bytes()
        self.pair(trust_fingerprint="c" * 64)
        self.assertEqual(path.read_bytes(), old)
        for change in ({"manifest": b"changed"}, {"bundle": b"changed"}, {"manifest_hash": "d" * 64}):
            self.fail(R.IMMUTABLE_RELEASE_CHANGED, self.pair, **change)
            self.assertEqual(path.read_bytes(), old)

    def test_corrupt_pair_is_never_replaced(self):
        self.pair()
        path = next(self.cache.store.directory.glob("pair-*.json"))
        original = json.loads(path.read_bytes())
        for value in ({}, dict(original, manifest="%%%"), dict(original, manifest="YQ=="),
                      dict(original, bundle=None)):
            path.write_text(json.dumps(value))
            self.fail(R.CACHE_INVALID, self.pair)
            self.assertEqual(json.loads(path.read_bytes()), value)
        with mock.patch.object(self.cache.store, "read", side_effect=[original, None]):
            self.fail(R.CACHE_INVALID, self.pair)

    def test_cache_permissions_and_atomic_failure_keep_previous(self):
        self.seed()
        directory = self.cache.store.directory
        path = directory / ("http-" + self.key + ".json")
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        previous = path.read_bytes()
        with mock.patch.object(hc.os, "replace", side_effect=OSError("disk canary")):
            self.transport.answers.append(HttpResponse(200, b"new"))
            self.fail(R.CACHE_INVALID, self.cache.fetch, URL)
        self.assertEqual(path.read_bytes(), previous)
        self.assertFalse(list(directory.glob(".pending-*")))
        name = "pair-" + self.key
        self.assertTrue(self.cache.store.write(name, {"a": 1}, exclusive=True))
        self.assertFalse(self.cache.store.write(name, {"a": 2}, exclusive=True))
        self.assertEqual(self.cache.store.read(name)["a"], 1)
        self.assertFalse(list(directory.glob(".pending-*")))

    def test_cache_symlinks_hardlinks_and_special_files_are_not_read(self):
        self.seed()
        path = self.cache.store.directory / ("http-" + self.key + ".json")
        outside = self.tmp / "outside"
        outside.write_text('{"schema":1,"secret":"CANARY"}')
        path.unlink()
        path.symlink_to(outside)
        self.assertIsNone(self.record())
        self.fail(R.CACHE_INVALID, self.cache.store.read, "http-" + self.key, strict=True)
        path.unlink()
        os.link(outside, path)
        self.assertIsNone(self.record())
        path.unlink()
        os.mkfifo(path)
        self.assertIsNone(self.record())
        path.unlink()
        path.mkdir()
        self.assertIsNone(self.record())
        path.rmdir()
        self.seed()
        path.chmod(0o644)
        self.assertIsNone(self.record())
        path.chmod(0o600)
        with path.open("wb") as handle:
            handle.truncate(hc.MAX_ENTRY_BYTES + 1)
        self.assertIsNone(self.record())

    def test_unsafe_directory_or_record_name_is_rejected(self):
        directory = self.tmp / "unsafe"
        directory.mkdir(mode=0o755)
        self.fail(R.CACHE_INVALID, hc.PrivateCache, directory)
        link = self.tmp / "link"
        link.symlink_to(self.cache.store.directory, target_is_directory=True)
        self.fail(R.CACHE_INVALID, hc.PrivateCache, link)
        self.fail(R.CACHE_INVALID, hc.PrivateCache, self.tmp / "missing" / "child")
        self.fail(R.CACHE_INVALID, self.cache.store.write, "../escape", {})
        self.cache.store.directory.chmod(0o755)
        self.fail(R.CACHE_INVALID, self.cache.store.write, "http-" + self.key, {})

    def test_cache_bounds_fail_closed_without_eviction(self):
        self.seed()
        previous = self.record()
        for constant in ("MAX_ENTRY_BYTES", "MAX_CACHE_BYTES", "MAX_CACHE_ENTRIES"):
            with mock.patch.object(hc, constant, 1):
                self.fail(R.CACHE_INVALID, self.cache.store.write, "http-" + self.key, {"body": "xx"})
        self.assertEqual(self.record(), previous)
        self.fail(R.CACHE_INVALID, hc._body, base64.b64encode(b"x" * (hc.MAX_DOWNLOAD_BYTES + 1)).decode())


if __name__ == "__main__":
    unittest.main()
