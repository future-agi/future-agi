"""Channel discovery with injected HTTPS and the real host Verifier adapter."""
import json
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from deploy.tests.test_update_trust import (BUNDLE_BYTES, COSIGN, FakeProbe, FakeRunner, FakeWhich,
                                            encode, make_manifest, raw_settings, release)
from deploy.update import discovery as ds
from deploy.update.contracts import ReasonCode as R
from deploy.update.httpcache import DiscoveryError, HttpCache, digest
from deploy.update.manifest import BUNDLE_ASSET, MANIFEST_ASSET, manifest_hash
from deploy.update.trust import HttpResponse, VerificationError, Verifier


class Web:
    def __init__(self):
        self.routes = {}
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        answer = self.routes.get(url, HttpResponse(404))
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(url, headers)
        return replace(answer, final_url=answer.final_url or url)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.web = Web()
        self.now = 1792800000.0
        self.cache = HttpCache(self.tmp / "cache", transport=self.web, clock=lambda: self.now)
        self.runner = FakeRunner()
        self.probe = FakeProbe()
        self.which = FakeWhich({"cosign": COSIGN, "docker": "/usr/bin/docker"})
        self.verifier = Verifier(raw_settings(), runner=self.runner, probe=self.probe, which=self.which,
                                 host_only=True, environ={})
        self.discovery = ds.ReleaseDiscovery(self.cache, self.verifier)
        self.listing([])

    def listing(self, records, *, page=1, link=None):
        url = f"{self.discovery.api}?per_page=100&page={page}"
        self.web.routes[url] = HttpResponse(200, json.dumps(records).encode(), '"list"', link=link)
        return url

    def pair(self, version="v1.2.3", *, manifest=None, bundle=BUNDLE_BYTES):
        url = f"https://github.com/future-agi/future-agi/releases/download/{version}/{MANIFEST_ASSET}"
        data = make_manifest(version) if manifest is None else manifest
        raw = data if isinstance(data, bytes) else encode(data)
        self.web.routes[url] = HttpResponse(200, raw, '"manifest"')
        sibling = url.rsplit("/", 1)[0] + "/" + BUNDLE_ASSET
        self.web.routes[sibling] = HttpResponse(200, bundle, '"bundle"')
        return url, sibling

    def record(self, version, **kwargs):
        return release(version, MANIFEST_ASSET, BUNDLE_ASSET, prerelease="-" in version, **kwargs)

    def discover(self, **kwargs):
        return self.discovery.discover(**dict({"current": "v1.2.0"}, **kwargs))

    def assertReason(self, result, reason):
        self.assertEqual(result.reason_codes, (reason,))
        self.assertFalse(result.complete)
        self.assertIsNone(result.checked_at)
        self.assertEqual(result.public_summary()["state"], "blocked")

    def test_local_publisher_origins_are_explicit_host_inputs_not_asset_urls(self):
        local = ds.ReleaseDiscovery(self.cache, self.verifier, api_origin="https://localhost:9443",
                                    asset_origin="https://localhost:9443")
        self.assertEqual(local.api, "https://localhost:9443/repos/future-agi/future-agi/releases")
        self.assertEqual(local._url("v1.2.3"),
                         "https://localhost:9443/future-agi/future-agi/releases/download/v1.2.3/" + MANIFEST_ASSET)
        self.assertNotEqual(local.source_identity_sha256, self.discovery.source_identity_sha256)
        self.assertEqual(self.discovery.api, "https://api.github.com/repos/future-agi/future-agi/releases")
        for bad in ("http://localhost:9443", "https://localhost:9443/untrusted-path", "https://user:pw@localhost"):
            with self.assertRaises(ValueError):
                ds.ReleaseDiscovery(self.cache, self.verifier, asset_origin=bad)

    def test_highest_complete_semver_not_api_order(self):
        versions = ["v1.3.0", "v1.2.4", "v1.9.0", "v1.2.3"]
        self.listing([self.record(v) for v in versions])
        for version in versions:
            self.pair(version)
        result = self.discover()
        self.assertTrue(result.complete)
        self.assertEqual(result.candidate.version, "v1.9.0")
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(result.candidate.manifest_hash, manifest_hash(make_manifest("v1.9.0")))
        self.assertEqual(result.candidate.manifest_sha256, digest(encode(make_manifest("v1.9.0"))))
        self.assertEqual(result.candidate.bundle_sha256, digest(BUNDLE_BYTES))
        self.assertEqual(result.public_summary()["state"], "available")
        self.assertNotIn("https", json.dumps(result.public_summary()))
        self.assertEqual(result.candidate.verification.signer,
                         self.verifier.expected_signer("v1.9.0"))
        self.assertTrue(all(call[0][0] == COSIGN for call in self.runner.calls))
        detached = result.candidate.manifest
        detached["version"] = "v9.9.9"
        self.assertEqual(result.candidate.version, "v1.9.0")

    def test_stable_and_beta_never_cross_channels(self):
        versions = ["v1.3.0", "v1.3.0-beta.2", "v1.3.0-rc.1", "v1.3.0-beta.10", "v1.2.9"]
        self.listing([self.record(v) for v in versions])
        for version in versions:
            self.pair(version)
        self.assertEqual(self.discover().candidate.version, "v1.3.0")
        self.assertEqual(self.discover(channel="beta").candidate.version, "v1.3.0-rc.1")
        self.listing([self.record("v1.3.0-beta.10"), self.record("v1.3.0-beta.2")])
        self.assertEqual(self.discover(channel="beta").candidate.version, "v1.3.0-beta.10")
        self.assertIsNone(self.discover().candidate)
        self.listing([self.record("v1.3.0")])
        result = self.discover(channel="beta")
        self.assertReason(result, R.RELEASE_INCOMPLETE)  # Previously accepted beta was withdrawn.
        self.assertIsNone(result.candidate)
        clean_cache = HttpCache(self.tmp / "clean-cache", transport=self.web, clock=lambda: self.now)
        clean = ds.ReleaseDiscovery(clean_cache, self.verifier)
        result = clean.discover(current="v1.2.0", channel="beta")
        self.assertTrue(result.complete)
        self.assertIsNone(result.candidate)

    def test_prerelease_metadata_drafts_and_bad_tag_grammar(self):
        self.listing([release("v1.3.0-beta.1", MANIFEST_ASSET, BUNDLE_ASSET),
                      release("v1.3.0", MANIFEST_ASSET, BUNDLE_ASSET, prerelease=True),
                      self.record("v1.4.0", draft=True), self.record("v01.3.0"),
                      self.record("v1.3.0-beta.01"), self.record("v1.3.0-rc1"), self.record("demo")])
        for channel in ("stable", "beta"):
            result = self.discover(channel=channel)
            self.assertTrue(result.complete)
            self.assertIsNone(result.candidate)
        self.assertEqual(self.runner.calls, [])

    def test_policy_exactly_matches_planner_and_never_downgrades(self):
        versions = ["v1.2.0", "v1.1.99", "v1.2.4", "v1.3.0", "v2.0.0"]
        self.listing([self.record(v) for v in versions])
        for version in versions:
            self.pair(version)
        for policy, expected in (("patch", "v1.2.4"), ("minor", "v1.3.0"), ("major", "v2.0.0")):
            with self.subTest(policy=policy):
                self.assertEqual(self.discover(policy=policy).candidate.version, expected)
        result = self.discover(current="v1.2.4", policy="patch")
        self.assertTrue(result.complete)
        self.assertIsNone(result.candidate)
        self.assertEqual(result.public_summary()["state"], "current")
        self.assertReason(self.discover(current="bad"), R.VERSION_POLICY_BLOCKED)
        self.assertReason(self.discover(policy="anything"), R.VERSION_POLICY_BLOCKED)
        self.assertReason(self.discover(channel="nightly"), R.CHANNEL_MISMATCH)

    def test_floor_selects_highest_supported_candidate_not_latest(self):
        latest = make_manifest("v1.5.0")
        latest["minimum_version"] = "v1.3.0"
        self.listing([self.record("v1.5.0"), self.record("v1.2.3")])
        self.pair("v1.5.0", manifest=latest)
        self.pair()
        self.assertEqual(self.discover().candidate.version, "v1.2.3")
        self.listing([self.record("v1.5.0")])
        self.assertReason(self.discover(current="v1.2.3"), R.VERSION_POLICY_BLOCKED)

    def test_incomplete_newest_exposes_older_but_blocks_automatic_selection(self):
        self.listing([release("v1.3.0", MANIFEST_ASSET), self.record("v1.2.3")])
        self.pair()
        result = self.discover()
        self.assertReason(result, R.RELEASE_INCOMPLETE)
        self.assertEqual(result.candidate.version, "v1.2.3")
        # An incomplete release outside policy does not block an eligible patch.
        self.assertTrue(self.discover(policy="patch").complete)
        self.listing([release("v1.3.0", MANIFEST_ASSET)])
        self.assertReason(self.discover(), R.RELEASE_INCOMPLETE)

    def test_missing_or_withdrawn_assets_block_without_using_old_bytes(self):
        for status in (404, 410):
            for part in (0, 1):
                with self.subTest(status=status, part=part):
                    urls = self.pair("v1.3.0")
                    self.listing([self.record("v1.3.0"), self.record("v1.2.3")])
                    self.pair()
                    self.assertEqual(self.discover().candidate.version, "v1.3.0")
                    self.web.routes[urls[part]] = HttpResponse(status)
                    result = self.discover()
                    self.assertReason(result, R.RELEASE_INCOMPLETE)
                    self.assertEqual(result.candidate.version, "v1.2.3")

    def test_previously_accepted_release_deleted_or_drafted_blocks_older(self):
        self.pair("v1.3.0")
        self.pair()
        self.listing([self.record("v1.3.0"), self.record("v1.2.3")])
        self.assertEqual(self.discover().candidate.version, "v1.3.0")
        for withdrawn in ([], [self.record("v1.3.0", draft=True)],
                          [release("v1.3.0", MANIFEST_ASSET, BUNDLE_ASSET, prerelease=True)]):
            self.listing([*withdrawn, self.record("v1.2.3")])
            result = self.discover()
            self.assertReason(result, R.RELEASE_INCOMPLETE)
            self.assertEqual(result.candidate.version, "v1.2.3")
        self.assertTrue(self.discover(policy="patch").complete)
        self.assertTrue(self.discover(current="v1.3.0").complete)

    def test_corrupt_accepted_identity_is_not_forgotten(self):
        self.pair()
        self.listing([self.record("v1.2.3")])
        self.assertTrue(self.discover().complete)
        path = next(self.cache.store.directory.glob("pair-*"))
        original = json.loads(path.read_bytes())
        for value in (dict(original, manifest="%%%"), dict(original, manifest="e30="),
                      dict(original, bundle_sha256="wrong")):
            path.write_text(json.dumps(value))
            self.assertReason(self.discover(), R.CACHE_INVALID)
        with mock.patch.object(self.cache.store, "read", return_value=None):
            with self.assertRaises(DiscoveryError):
                self.cache.known_versions(self.discovery._url, channel="stable", source_identity="")

    def test_clock_rollback_during_verification_blocks_fresh_candidate(self):
        self.pair()
        self.listing([self.record("v1.2.3")])

        def rewind(_):
            self.now -= 60

        self.runner.on_call = rewind
        self.assertReason(self.discover(), R.CLOCK_SKEW)

    def test_key_swap_for_copy_then_restored_still_rejected(self):
        key = self.tmp / "trust.pub"
        key.write_bytes(b"pinned")
        self.verifier.settings = raw_settings(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key))
        self.pair()
        self.listing([self.record("v1.2.3")])
        from deploy.update import trust
        read = trust._read_local

        def swapped(path, what=""):
            if what == "FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY":
                return b"other-key-at-copy-time"
            return read(path, what)

        with mock.patch.object(trust, "_read_local", side_effect=swapped):
            self.assertReason(self.discover(), R.SIGNATURE_REJECTED)
        self.assertEqual(list(self.cache.store.directory.glob("pair-*")), [])

    def test_bad_signature_never_downgrades(self):
        self.listing([self.record("v1.3.0"), self.record("v1.2.3")])
        self.pair("v1.3.0")
        self.pair()
        self.runner.returncode = 1
        self.runner.stderr = "CANARY https://user:secret@host/path"
        result = self.discover()
        self.assertReason(result, R.SIGNATURE_REJECTED)
        self.assertIsNone(result.candidate)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertNotIn("CANARY", json.dumps(result.public_summary()))

    def test_unsupported_newer_schema_never_downgrades(self):
        data = make_manifest("v1.3.0")
        data["schema_version"] = 99
        self.pair("v1.3.0", manifest=data)
        self.pair()
        self.listing([self.record("v1.3.0"), self.record("v1.2.3")])
        self.assertReason(self.discover(), R.UNSUPPORTED_CONTRACT)
        self.assertEqual(self.runner.calls, [])

    def test_schema_two_is_verified_by_shared_validator(self):
        data = make_manifest()
        data["schema_version"] = 2
        # S02's conservative v2 default carries no automatic certification.
        data["compatibility"] = {"contract_version": 1, "automatic_apply": False, "automatic_rollback": {"strategy": "never"},
                                 "requires_operator_initialization": True, "predecessors": [], "state_domains": [],
                                 "required_env": [], "validation": None}
        self.pair(manifest=data)
        self.listing([self.record("v1.2.3")])
        result = self.discover()
        self.assertTrue(result.complete, result.public_summary())
        self.assertEqual(result.candidate.manifest["schema_version"], 2)

    def test_warm_304_verifies_again_and_current_signer_changes_fail(self):
        data = make_manifest(identity=self.verifier.expected_signer("v1.2.3"))
        urls = self.pair(manifest=data)
        list_url = self.listing([self.record("v1.2.3")])
        self.assertTrue(self.discover().complete)
        self.now += 60
        for url in (list_url, *urls):
            self.web.routes[url] = HttpResponse(304)
        result = self.discover()
        self.assertTrue(result.complete)
        self.assertEqual(len(self.runner.calls), 2)
        self.assertEqual(result.checked_at.timestamp(), self.now)
        self.assertTrue(all("If-None-Match" in call[1] for call in self.web.calls[-3:]))
        self.verifier.settings = raw_settings(FUTURE_AGI_UPDATE_TRUSTED_IDENTITY="https://different.test/{version}")
        self.assertReason(self.discover(), R.SIGNATURE_REJECTED)
        self.assertEqual(len(self.runner.calls), 2)

    def test_same_key_path_rotation_changes_anchor_and_reverifies(self):
        key = self.tmp / "trust.pub"
        key.write_bytes(b"old key")
        self.verifier.settings = raw_settings(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key),
                                              FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true")
        self.listing([self.record("v1.2.3")])
        urls = self.pair()
        old = self.discover().candidate.trust_fingerprint
        for url in urls:
            self.web.routes[url] = HttpResponse(304)
        key.write_bytes(b"new key")
        copied = []

        def see_key(command):
            copied.append(Path(command[command.index("--key") + 1]).read_bytes())

        self.runner.on_call = see_key
        result = self.discover()
        self.assertTrue(result.complete)
        self.assertNotEqual(old, result.candidate.trust_fingerprint)
        self.assertEqual(copied, [b"new key"])
        self.runner.returncode = 1
        self.assertReason(self.discover(), R.SIGNATURE_REJECTED)

    def test_trust_change_during_verification_fails_closed(self):
        self.listing([self.record("v1.2.3")])
        self.pair()

        def rotate(_):
            self.verifier.settings = raw_settings(FUTURE_AGI_UPDATE_TRUSTED_ISSUER="https://other.test")

        self.runner.on_call = rotate
        self.assertReason(self.discover(), R.SIGNATURE_REJECTED)
        self.assertEqual(list(self.cache.store.directory.glob("pair-*")), [])

    def test_changed_bytes_same_etag_preserves_original_immutable_pair(self):
        self.listing([self.record("v1.2.3")])
        urls = self.pair()
        self.assertTrue(self.discover().complete)
        path = next(self.cache.store.directory.glob("pair-*"))
        original = path.read_bytes()
        self.web.routes[urls[0]] = replace(self.web.routes[urls[0]], body=encode(make_manifest()) + b"\n")
        self.assertReason(self.discover(), R.IMMUTABLE_RELEASE_CHANGED)
        self.assertEqual(path.read_bytes(), original)
        self.pair(bundle=BUNDLE_BYTES + b" ")
        self.assertReason(self.discover(), R.IMMUTABLE_RELEASE_CHANGED)
        self.assertEqual(path.read_bytes(), original)

    def test_manifest_tag_channel_and_bundle_binding(self):
        self.listing([self.record("v1.2.3")])
        self.pair(manifest=make_manifest("v1.2.4"))
        self.assertReason(self.discover(), R.IMMUTABLE_RELEASE_CHANGED)
        self.pair(manifest=make_manifest(channel="beta"))
        self.assertReason(self.discover(), R.CHANNEL_MISMATCH)
        data = make_manifest(bundle="other.json")
        self.pair(manifest=data)
        other = "https://github.com/future-agi/future-agi/releases/download/v1.2.3/other.json"
        self.web.routes[other] = HttpResponse(200, BUNDLE_BYTES)
        self.assertReason(self.discover(), R.RELEASE_INCOMPLETE)
        self.assertEqual(self.runner.calls, [])

    def test_pagination_unsorted_backport_on_second_page(self):
        link = f'<{self.discovery.api}?page=2&per_page=100>; rel="next", <{self.discovery.api}?page=2&per_page=100>; rel="last"'
        self.listing([self.record("v1.2.3")], link=link)
        self.listing([self.record("v1.9.0")], page=2)
        self.pair()
        self.pair("v1.9.0")
        self.assertEqual(self.discover().candidate.version, "v1.9.0")
        # Even without a Link header a full page cannot claim complete search.
        self.listing([self.record(f"v1.0.{n}") for n in range(100)])
        self.assertEqual(self.discover().candidate.version, "v1.9.0")

    def test_five_full_pages_fail_incomplete_without_verifying(self):
        for page in range(1, 6):
            self.listing([self.record(f"v1.{page}.{n}") for n in range(100)], page=page)
        self.assertReason(self.discover(), R.DISCOVERY_INCOMPLETE)
        self.assertEqual(len(self.web.calls), 5)
        self.assertEqual(self.runner.calls, [])

    def test_twenty_signature_budget_does_not_pick_unsearched_release(self):
        versions = [f"v1.3.{n}" for n in range(21)]
        self.listing([self.record(v) for v in versions])
        for version in versions:
            data = make_manifest(version)
            data["minimum_version"] = "v1.3.0"
            self.pair(version, manifest=data)
        self.assertReason(self.discover(), R.DISCOVERY_INCOMPLETE)
        self.assertEqual(len(self.runner.calls), 20)

    def test_bad_pagination_never_follows_attacker_url(self):
        base = self.discovery.api
        links = ["invalid", f'<{base}?page=2&per_page=100&token=SECRET>; rel="next"',
                 '<https://attacker.test/releases?page=2&per_page=100>; rel="next"',
                 f'<{base}?page=3&per_page=100>; rel="next"', f'<{base}?page=2&per_page=30>; rel="next"',
                 f'<{base}?page=2&page=2&per_page=100>; rel="next"',
                 f'<{base}?page=02&per_page=100>; rel="next"',
                 f'<{base}?bad>; rel="next"',
                 f'<{base}?page=2&per_page=100>; rel="next", <{base}?page=2&per_page=100>; rel="next"',
                 '<http://api.github.com/repos/future-agi/future-agi/releases?page=2&per_page=100>; rel="next"']
        for link in links:
            with self.subTest(link=link):
                before = len(self.web.calls)
                self.listing([], link=link)
                self.assertReason(self.discover(), R.DISCOVERY_INCOMPLETE)
                self.assertEqual(len(self.web.calls), before + 1)

    def test_malformed_release_list_never_claims_current(self):
        values = ({"message": "CANARY"}, [None], [dict(self.record("v1.2.3"), draft="false")],
                  [self.record("v1.2.3"), release("v1.2.3")], [self.record("v1.2.3")] * 101)
        url = self.listing([])
        for value in values:
            self.web.routes[url] = HttpResponse(200, json.dumps(value).encode())
            self.assertReason(self.discover(), R.DISCOVERY_INCOMPLETE)
        for raw in (b"{bad", b"[" * 2000):
            self.web.routes[url] = HttpResponse(200, raw)
            self.assertReason(self.discover(), R.DISCOVERY_INCOMPLETE)

    def test_duplicate_identical_records_and_ad_hoc_releases(self):
        self.listing([self.record("v1.2.3"), self.record("v1.2.3"), self.record("demo")])
        self.pair()
        self.assertEqual(self.discover().candidate.version, "v1.2.3")

    def test_offline_rate_limit_clock_and_malformed_pair_are_closed(self):
        url = self.listing([self.record("v1.2.3")])
        pair = self.pair()
        self.assertTrue(self.discover().complete)
        self.web.routes[url] = OSError("CANARY /secret/path")
        result = self.discover()
        self.assertReason(result, R.NETWORK_UNAVAILABLE)
        self.assertIsNone(result.candidate)
        self.assertNotIn("CANARY", json.dumps(result.public_summary()))
        self.listing([self.record("v1.2.3")])
        self.web.routes[pair[1]] = HttpResponse(429, retry_after="100")
        result = self.discover()
        self.assertReason(result, R.RATE_LIMITED)
        self.assertEqual(result.retry_at, self.now + 100)
        self.now -= 10
        self.assertReason(self.discover(), R.CLOCK_SKEW)
        self.now += 111
        for raw in (b"{", b"{}", b"[]", b"[" * 2000):
            self.pair(manifest=raw)
            self.assertReason(self.discover(), R.MANIFEST_UNAVAILABLE if raw != b"{}" else R.UNSUPPORTED_CONTRACT)

    def test_missing_host_verifier_never_pulls_or_runs_helper_or_network(self):
        for found, version, host_only in (({"docker": "/bin/docker"}, "v3.1.3", True),
                                         ({"cosign": COSIGN, "docker": "/bin/docker"}, "v2.4.2", True),
                                         ({"cosign": COSIGN}, "devel", True),
                                         ({"cosign": COSIGN}, "v3.1.3", False)):
            self.which.found = found
            self.probe.version = version
            self.verifier.host_only = host_only
            self.assertReason(self.discover(), R.HOST_VERIFIER_REQUIRED)
        self.assertEqual(self.web.calls, [])
        self.assertEqual(self.runner.calls, [])
        self.assertNotIn("docker", self.which.calls)

    def test_host_is_reprobed_each_tick_and_timeouts_are_bounded(self):
        self.listing([self.record("v1.2.3")])
        self.pair()
        self.assertTrue(self.discover().complete)
        self.assertTrue(self.discover().complete)
        self.assertEqual(len(self.probe.calls), 2)
        for _, kwargs in self.runner.calls + self.probe.calls:
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 120)
        self.probe.error = OSError("missing")
        self.assertReason(self.discover(), R.HOST_VERIFIER_REQUIRED)

    def test_exact_refresh_no_latest_index_and_304_reverifies(self):
        urls = self.pair()
        result = self.discovery.refresh("v1.2.3")
        self.assertTrue(result.complete)
        self.assertEqual([call[0] for call in self.web.calls], list(urls))
        self.now += 10
        for url in urls:
            self.web.routes[url] = HttpResponse(304)
        self.assertTrue(self.discovery.refresh("v1.2.3").complete)
        self.assertEqual(len(self.runner.calls), 2)
        self.assertReason(self.discovery.refresh("v1.2.3", channel="beta"), R.CHANNEL_MISMATCH)
        self.assertReason(self.discovery.refresh("v1.2.3", channel="nightly"), R.CHANNEL_MISMATCH)
        self.web.routes[urls[0]] = OSError("offline")
        self.assertReason(self.discovery.refresh("v1.2.3"), R.NETWORK_UNAVAILABLE)

    def test_total_budget_and_verifier_errors_never_claim_fresh(self):
        self.discovery.monotonic = mock.Mock(side_effect=[0, 121])
        self.assertReason(self.discover(), R.DISCOVERY_INCOMPLETE)
        self.discovery.monotonic = time.monotonic
        with mock.patch.object(self.verifier, "begin_discovery", side_effect=VerificationError("discovery_incomplete")):
            self.assertReason(self.discover(), R.DISCOVERY_INCOMPLETE)
        self.listing([self.record("v1.2.3")])
        self.pair()
        with mock.patch.object(self.verifier, "verify", side_effect=OSError("CANARY")):
            self.assertReason(self.discover(), R.MANIFEST_UNAVAILABLE)
        with mock.patch.object(self.verifier, "verify", side_effect=VerificationError("discovery_incomplete")):
            self.assertReason(self.discovery.refresh("v1.2.3"), R.DISCOVERY_INCOMPLETE)
        self.runner.error = subprocess.TimeoutExpired("cosign", 120)
        self.assertReason(self.discover(), R.SIGNATURE_REJECTED)

    def test_repository_validation_and_missing_bundle_copy_are_closed(self):
        for value in ("../../other", "user/repo?token=SECRET", "user/repo/other", None):
            with self.assertRaises(DiscoveryError):
                ds.ReleaseDiscovery(self.cache, self.verifier, repository=value)
        self.listing([self.record("v1.2.3")])
        self.pair()
        from deploy.update import trust
        original = trust._write_private

        def disk_full(path, raw):
            if path.name == "bundle.json":
                raise OSError("CANARY")
            return original(path, raw)

        with mock.patch.object(trust, "_write_private", side_effect=disk_full):
            self.assertReason(self.discover(), R.RELEASE_INCOMPLETE)
        self.assertEqual(self.runner.calls, [])


if __name__ == "__main__":
    unittest.main()
