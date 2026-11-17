"""PostgreSQL concurrency contracts for the console-run signer key cap."""

import base64
from functools import partial
from queue import Queue
from threading import Barrier, Event, Thread
from time import monotonic, time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.db import connection, connections, transaction

from accounts.models.service_grant import ServiceGrant
from agentcc.models.console_run_signer_key import (
    MAX_ACTIVE_PER_DEPLOYMENT,
    AgentccConsoleRunSignerKey,
)
from agentcc.services.console_run_signer import (
    GRANT_REUSE_MARGIN,
    _grant,
    _run_authorization,
)
from agentcc.services.console_run_signer_keys import SignerKeyRefused, add_key
from agentcc.tests.test_console_run_sign_hardening import authorization
from agentcc.tests.test_console_run_sign_hardening import configured as configured

pytestmark = pytest.mark.django_db(transaction=True)

CAP_REFUSAL = "This deployment already has two active keys; revoke one first."
BARRIER_TIMEOUT = 10
JOIN_TIMEOUT = 30


@pytest.fixture(autouse=True)
def _postgresql_transactions():
    assert connection.vendor == "postgresql", "Run this contract on PostgreSQL"
    assert not connection.in_atomic_block
    assert connection.get_autocommit()


def _new_public_key():
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _add(deployment, kid, public_key):
    return add_key(
        deployment=deployment,
        kid=kid,
        public_key=public_key,
        operator="ops@example.test",
    ).kid


def _run_concurrently(operations):
    """Start real database sessions together and report failures to pytest."""
    start = Barrier(len(operations), timeout=BARRIER_TIMEOUT)
    outcomes = Queue()

    def worker(index, operation):
        try:
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET statement_timeout = '15s'")
                    cursor.execute("SET lock_timeout = '10s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pid = cursor.fetchone()[0]
                start.wait()
                try:
                    result = operation()
                except SignerKeyRefused as exc:
                    result = exc
            finally:
                connections.close_all()
        except Exception as exc:
            start.abort()
            outcomes.put((index, None, exc))
        else:
            outcomes.put((index, backend_pid, result))

    threads = [
        Thread(
            target=worker,
            args=(index, operation),
            name=f"signer-key-writer-{index}",
            daemon=True,
        )
        for index, operation in enumerate(operations)
    ]
    for thread in threads:
        thread.start()
    deadline = monotonic() + JOIN_TIMEOUT
    for thread in threads:
        thread.join(timeout=max(0, deadline - monotonic()))
    hung = [thread.name for thread in threads if thread.is_alive()]
    assert not hung, f"Signer key workers did not finish within {JOIN_TIMEOUT}s: {hung}"
    assert outcomes.qsize() == len(threads), "A signer key worker lost its outcome"
    records = sorted(outcomes.get_nowait() for _ in threads)
    errors = [
        f"worker {index}: {result!r}"
        for index, backend_pid, result in records
        if backend_pid is None
    ]
    assert not errors, f"Unexpected signer key worker failures: {errors}"
    assert len({backend_pid for _, backend_pid, _ in records}) == len(threads), (
        "Concurrent writers must use distinct PostgreSQL sessions"
    )
    return [result for _, _, result in records]


def _race_adds(deployment, kids):
    operations = [partial(_add, deployment, kid, _new_public_key()) for kid in kids]
    return _run_concurrently(operations)


def _assert_capacity(deployment, kids, results, existing=()):
    successful = {
        kid for kid, result in zip(kids, results, strict=True) if result == kid
    }
    refused = [
        (kid, result)
        for kid, result in zip(kids, results, strict=True)
        if isinstance(result, SignerKeyRefused)
    ]
    expected_new = MAX_ACTIVE_PER_DEPLOYMENT - len(existing)
    assert len(successful) == expected_new
    assert len(refused) == len(kids) - expected_new
    assert all(str(error) == CAP_REFUSAL for _, error in refused)

    rows = AgentccConsoleRunSignerKey.objects.using("default").filter(
        deployment=deployment
    )
    active = rows.filter(revoked_at__isnull=True)
    assert active.count() == MAX_ACTIVE_PER_DEPLOYMENT
    assert set(active.values_list("kid", flat=True)) == set(existing) | successful
    assert rows.count() == MAX_ACTIVE_PER_DEPLOYMENT
    assert (
        not AgentccConsoleRunSignerKey.objects.using("default")
        .filter(kid__in=[kid for kid, _ in refused])
        .exists()
    )


def test_concurrent_adds_enforce_two_active_keys_per_deployment():
    deployment = "concurrent-empty"
    kids = [f"concurrent-empty-k{index}" for index in range(4)]

    results = _race_adds(deployment, kids)

    _assert_capacity(deployment, kids, results)


def test_different_deployments_do_not_block_each_other():
    deployments = ("concurrent-independent-a", "concurrent-independent-b")
    keys = [
        [
            (f"{deployment}-k{index}", _new_public_key())
            for index in range(MAX_ACTIVE_PER_DEPLOYMENT)
        ]
        for deployment in deployments
    ]
    both_full = Barrier(len(deployments), timeout=BARRIER_TIMEOUT)

    def fill_deployment(deployment, key_pairs):
        # Keep each deployment's advisory lock until both have reached the cap.
        # A global lock cannot satisfy this barrier before either transaction ends.
        with transaction.atomic():
            added = tuple(_add(deployment, kid, public) for kid, public in key_pairs)
            both_full.wait()
        return added

    results = _run_concurrently(
        [
            partial(fill_deployment, deployment, key_pairs)
            for deployment, key_pairs in zip(deployments, keys, strict=True)
        ]
    )

    for deployment, key_pairs, result in zip(deployments, keys, results, strict=True):
        assert result == tuple(kid for kid, _ in key_pairs)
        rows = AgentccConsoleRunSignerKey.objects.using("default").filter(
            deployment=deployment
        )
        active = rows.filter(revoked_at__isnull=True)
        assert active.count() == MAX_ACTIVE_PER_DEPLOYMENT
        assert set(active.values_list("kid", flat=True)) == set(result)
        assert rows.count() == MAX_ACTIVE_PER_DEPLOYMENT


def test_one_active_key_allows_only_one_of_two_concurrent_adds():
    deployment = "concurrent-existing"
    existing = "concurrent-existing-k0"
    assert _add(deployment, existing, _new_public_key()) == existing
    kids = ["concurrent-existing-k1", "concurrent-existing-k2"]

    results = _race_adds(deployment, kids)

    _assert_capacity(deployment, kids, results, existing=(existing,))


def _run_behind_held_lock(acquire_lock, operations, while_held=lambda: None):
    """Prove workers actually wait on PostgreSQL locks before letting them run."""
    pids = Queue()
    outcomes = Queue()

    def worker(index, operation):
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SET statement_timeout = '15s'")
                cursor.execute("SET lock_timeout = '10s'")
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])
            try:
                result = operation()
            except SignerKeyRefused as exc:
                result = exc
            outcomes.put((index, result))
        except Exception as exc:
            outcomes.put((index, exc))
        finally:
            connections.close_all()

    threads = [
        Thread(target=worker, args=(i, op), daemon=True)
        for i, op in enumerate(operations)
    ]
    try:
        with transaction.atomic():
            acquire_lock()
            for thread in threads:
                thread.start()
            backend_pids = [pids.get(timeout=5) for _ in threads]
            assert len(set(backend_pids)) == len(threads)
            deadline = monotonic() + 5
            for pid in backend_pids:
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [pid])
                        blockers = cursor.fetchone()[0]
                    if blockers:
                        break
                    assert outcomes.empty(), "A worker bypassed the held lock"
                    assert monotonic() < deadline, "Worker never waited on the lock"
                    Event().wait(0.02)
            assert outcomes.empty(), "No worker may finish while the lock is held"
            while_held()
    finally:
        # Exiting atomic releases the lock even if an assertion failed above.
        deadline = monotonic() + JOIN_TIMEOUT
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=max(0, deadline - monotonic()))
        assert not any(t.is_alive() for t in threads), "Lock workers did not finish"
    results = sorted(outcomes.get(timeout=1) for _ in threads)
    return [result for _, result in results]


def test_add_waits_for_the_deployment_lock_and_rechecks_capacity():
    deployment = "held-deployment-lock"
    existing = "held-k0"
    _add(deployment, existing, _new_public_key())
    new_key = _new_public_key()

    def hold():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ["console-run-signer-key:" + deployment],
            )

    results = _run_behind_held_lock(
        hold,
        [partial(_add, deployment, "waiting-k", new_key)],
        # The holder fills the final slot before the waiter can count keys.
        lambda: _add(deployment, "held-k1", _new_public_key()),
    )
    assert len(results) == 1
    assert isinstance(results[0], SignerKeyRefused)
    assert str(results[0]) == CAP_REFUSAL
    assert set(
        AgentccConsoleRunSignerKey.objects.filter(
            deployment=deployment, revoked_at__isnull=True
        ).values_list("kid", flat=True)
    ) == {existing, "held-k1"}


def test_concurrent_initial_mints_reuse_one_grant(user, workspace):
    token, row = authorization(user, workspace)
    now = int(time())
    claims = _run_authorization(token, "ee-prod-1", now)

    def hold():
        ServiceGrant.objects.select_for_update().get(pk=row.pk)

    results = _run_behind_held_lock(
        hold, [partial(_grant, claims, now), partial(_grant, claims, now)]
    )
    assert all(isinstance(result, tuple) for result in results)
    # Match the public sign response, which normalizes the stored expiry to int.
    normalized = [(token, int(expiry)) for token, expiry in results]
    assert normalized[0] == normalized[1]
    grants = ServiceGrant.objects.filter(kind="console-run", sub=claims["sub"])
    assert grants.count() == 1
    assert int(grants.get().exp) == normalized[0][1]


def test_reusable_authorization_can_renew_near_grant_expiry(user, workspace):
    token, _ = authorization(user, workspace)
    now = int(time())
    claims = _run_authorization(token, "ee-prod-1", now)
    first, expiry = _grant(claims, now)
    # The concurrency invariant is not a lifetime limit on the authorization.
    second, renewed_expiry = _grant(claims, int(expiry) - GRANT_REUSE_MARGIN)
    assert second != first
    assert int(renewed_expiry) <= int(claims["exp"])
    assert (
        ServiceGrant.objects.filter(kind="console-run", sub=claims["sub"]).count() == 2
    )
