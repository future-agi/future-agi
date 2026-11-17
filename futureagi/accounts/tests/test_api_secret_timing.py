"""A wrong secret and a right one take the same time to refuse or accept (READINESS L03).

L03's second proving test for API secrets: n = 10,000 of each, interleaved in a
random order, on a sealed key. It is a timing test, not proof against a remote
attacker (the T6 pen test covers that). Two layers are measured:

* ``verify_secret``, the comparison itself, where a leak would be visible;
* ``find_api_key`` against real Postgres, what a request pays.

A negative control shows the harness can see a difference of one HMAC: a miss
that skipped the HMAC would be measurably faster, so "no difference" means
something. Set API_SECRET_TIMING_RECEIPT=<path> to write the measured values.
"""

import gc
import hmac
import json
import os
import random
import statistics
import time

import pytest

from accounts import api_secrets
from accounts.api_key_lookup import find_api_key
from accounts.models import OrgApiKey

N = 10_000
# The medians of right and wrong may differ by at most this fraction.
TOLERANCE = 0.10


def _measure(operations):
    """Run (label, fn) pairs in the given order; per-label durations in ns."""
    durations = {}
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        for label, fn in operations:
            start = time.perf_counter_ns()
            fn()
            durations.setdefault(label, []).append(time.perf_counter_ns() - start)
    finally:
        if gc_was_enabled:
            gc.enable()
    return durations


def _summary(samples):
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "median_ns": statistics.median(ordered),
        "p05_ns": ordered[len(ordered) // 20],
        "p95_ns": ordered[len(ordered) * 19 // 20],
        "mad_ns": statistics.median(
            abs(x - statistics.median(ordered)) for x in ordered
        ),
    }


def _interleaved(pairs, n=N, seed=1729):
    operations = [pair for pair in pairs for _ in range(n)]
    random.Random(seed).shuffle(operations)
    for _label, fn in pairs:  # warm-up, not measured
        for _ in range(200):
            fn()
    return _measure(operations)


def _relative_gap(a, b):
    return abs(a["median_ns"] - b["median_ns"]) / max(a["median_ns"], b["median_ns"])


def _write_receipt(name, payload):
    path = os.environ.get("API_SECRET_TIMING_RECEIPT")
    if not path:
        return
    receipts = {}
    if os.path.exists(path):
        with open(path) as handle:
            receipts = json.load(handle)
    receipts[name] = payload
    with open(path, "w") as handle:
        json.dump(receipts, handle, indent=2, sort_keys=True)


def test_verify_secret_takes_the_same_time_for_a_wrong_and_a_right_secret():
    secret = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    stored = api_secrets.hash_secret(secret)
    wrong_first = "f" + secret[1:]  # differs in the first character
    wrong_last = secret[:-1] + "1"  # differs only in the last character
    durations = _interleaved(
        [
            ("right", lambda: api_secrets.verify_secret(stored, "", secret)),
            (
                "wrong_first_char",
                lambda: api_secrets.verify_secret(stored, "", wrong_first),
            ),
            (
                "wrong_last_char",
                lambda: api_secrets.verify_secret(stored, "", wrong_last),
            ),
            ("unknown_key", lambda: api_secrets.verify_secret("", "", secret)),
        ]
    )
    summary = {label: _summary(samples) for label, samples in durations.items()}
    gaps = {
        label: _relative_gap(summary["right"], summary[label])
        for label in ("wrong_first_char", "wrong_last_char", "unknown_key")
    }

    # Negative control: the miss path as written, and the same path without its
    # one HMAC (what a naive "return early for an unknown key" would cost).
    def miss_without_hmac():
        api_secrets.peppers()
        hmac.compare_digest(api_secrets._ABSENT_DIGEST, api_secrets._ABSENT_DIGEST)
        return api_secrets.Verdict(False)

    control = _interleaved(
        [
            ("with_hmac", lambda: api_secrets.verify_secret("", "", secret)),
            ("without_hmac", miss_without_hmac),
        ]
    )
    control_summary = {label: _summary(samples) for label, samples in control.items()}
    control_gap = _relative_gap(
        control_summary["with_hmac"], control_summary["without_hmac"]
    )
    _write_receipt(
        "verify_secret",
        {
            "summary": summary,
            "relative_median_gap": gaps,
            "tolerance": TOLERANCE,
            "negative_control": {
                "summary": control_summary,
                "relative_median_gap": control_gap,
            },
        },
    )
    assert control_gap > TOLERANCE, (
        "the harness cannot see one HMAC; the test proves nothing"
    )
    for label, gap in gaps.items():
        assert gap <= TOLERANCE, (label, gap, summary)


@pytest.mark.django_db
def test_find_api_key_takes_the_same_time_for_a_wrong_and_a_right_secret(user):
    key = OrgApiKey.no_workspace_objects.create(
        name="timing", organization=user.organization, type="user", user=user
    )
    secret = key.secret_key
    wrong = secret[:-1] + ("0" if secret[-1] != "0" else "1")
    queryset = OrgApiKey.no_workspace_objects.filter(enabled=True, deleted=False)
    unknown = "no-such-api-key-" + secret
    durations = _interleaved(
        [
            ("right", lambda: find_api_key(queryset, key.api_key, secret)),
            ("wrong", lambda: find_api_key(queryset, key.api_key, wrong)),
            # Recorded, not asserted: whether an api_key exists is not a secret
            # (it is the public half of the pair), and Postgres decides this one.
            ("unknown_key", lambda: find_api_key(queryset, unknown, secret)),
        ]
    )
    summary = {label: _summary(samples) for label, samples in durations.items()}
    gap = _relative_gap(summary["right"], summary["wrong"])
    _write_receipt(
        "find_api_key_postgres",
        {
            "summary": summary,
            "relative_median_gap": gap,
            "tolerance": TOLERANCE,
            "unknown_key_relative_median_gap": _relative_gap(
                summary["right"], summary["unknown_key"]
            ),
        },
    )
    assert find_api_key(queryset, key.api_key, secret) is not None
    assert find_api_key(queryset, key.api_key, wrong) is None
    assert gap <= TOLERANCE, (gap, summary)


_PATHS = (
    "hash_right",
    "hash_wrong",
    "legacy_right",
    "legacy_wrong",
    "unknown_key",
    "malformed_hash",
)


def _verify_args(path):
    """(stored hash, legacy plaintext, presented) for one path through verify_secret."""
    secret = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    wrong = "f" + secret[1:]
    return {
        "hash_right": (api_secrets.hash_secret(secret), "", secret),
        "hash_wrong": (api_secrets.hash_secret(secret), "", wrong),
        "legacy_right": ("", secret, secret),
        "legacy_wrong": ("", secret, wrong),
        "unknown_key": ("", "", secret),
        "malformed_hash": ("hmac-sha256$zz$not-a-digest", "", secret),
    }[path]


@pytest.mark.parametrize("path", _PATHS)
def test_one_compare_digest_alone_decides_every_verdict(monkeypatch, path):
    """The constant-time property itself, pinned where the timing tests cannot see.

    The timing tests above are a coarse regression guard: in the verifier's runs
    of this harness (n = 10,000) an early-exit byte loop showed an 11.3% gap and
    is caught, but a plain ``==`` showed 1.6%, one 41.7 ns tick, inside the 10%
    tolerance. So the mechanism is checked directly: every path makes exactly one
    ``hmac.compare_digest`` call, and on the paths that can succeed, forcing that
    call's answer forces the verdict, wrong secret included. A ``==`` in its
    place, or a second comparison beside it, fails here.
    """
    real = hmac.compare_digest
    args = _verify_args(path)
    can_succeed = path.startswith(("hash_", "legacy_"))
    for forced in (True, False):
        calls = []

        def compare(a, b, forced=forced, calls=calls):
            calls.append(real(a, b))
            return forced

        monkeypatch.setattr(api_secrets.hmac, "compare_digest", compare)
        verdict = api_secrets.verify_secret(*args)
        monkeypatch.undo()
        assert len(calls) == 1, (path, forced, calls)
        assert verdict.ok is (forced and can_succeed), (path, forced)
