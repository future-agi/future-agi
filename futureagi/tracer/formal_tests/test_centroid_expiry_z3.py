"""
Z3 proofs for the cluster-centroid expiry invariants (issue #306).

Models the TTL-based expiry logic as a transition system and proves:
1. A centroid with last_updated < (now - TTL_DAYS) is eligible for deletion.
2. A centroid with last_updated >= (now - TTL_DAYS) must NOT be deleted.
3. TTL boundary is exact — membership flips at exactly TTL_DAYS.
4. Monotone: raising TTL_DAYS never causes an otherwise-retained centroid to expire.
5. Reducing TTL_DAYS never causes an otherwise-expired centroid to be retained.
"""

from z3 import (
    And,
    BitVec,
    BitVecVal,
    BoolSort,
    Function,
    Int,
    IntSort,
    Not,
    Or,
    Solver,
    UGE,
    ULE,
    ULT,
    sat,
    unsat,
)


def test_expired_centroid_eligible_for_deletion():
    """A centroid older than TTL is always eligible for deletion."""
    s = Solver()

    now = Int("now")          # current Unix timestamp (seconds)
    last_updated = Int("last_updated")
    ttl_days = Int("ttl_days")

    ttl_seconds = ttl_days * 86400
    is_expired = last_updated + ttl_seconds < now
    is_eligible = is_expired  # same predicate in our model

    s.add(ttl_days > 0)
    s.add(now > 0)
    s.add(last_updated >= 0)
    s.add(last_updated + ttl_seconds < now)  # enforce expired

    # Claim: eligible is True — check satisfiability (not unsat)
    s.add(Not(is_eligible))
    assert s.check() == unsat, "Expired centroid must be eligible for deletion"


def test_fresh_centroid_not_eligible():
    """A centroid last updated within TTL must not be deleted."""
    s = Solver()

    now = Int("now")
    last_updated = Int("last_updated")
    ttl_days = Int("ttl_days")

    ttl_seconds = ttl_days * 86400
    is_expired = last_updated + ttl_seconds < now
    is_eligible = is_expired

    s.add(ttl_days > 0)
    s.add(now > 0)
    s.add(last_updated >= 0)
    s.add(last_updated + ttl_seconds >= now)  # within TTL

    # Claim: not eligible — check satisfiability (eligible should be unsat here)
    s.add(is_eligible)
    assert s.check() == unsat, "Fresh centroid must not be eligible for deletion"


def test_ttl_boundary_is_exclusive():
    """At exactly now - TTL_DAYS seconds, the centroid is still fresh (not expired)."""
    s = Solver()

    now = Int("now")
    last_updated = Int("last_updated")
    ttl_days = Int("ttl_days")

    ttl_seconds = ttl_days * 86400
    is_expired = last_updated + ttl_seconds < now

    s.add(ttl_days == 90)
    s.add(now == 1_000_000_000)
    s.add(last_updated == now - ttl_seconds)  # exactly at boundary

    s.add(is_expired)
    assert s.check() == unsat, "At exactly TTL boundary, centroid should NOT be expired"


def test_raising_ttl_never_expires_retained_centroid():
    """Increasing TTL_DAYS cannot cause a currently-retained centroid to expire."""
    s = Solver()

    now = Int("now")
    last_updated = Int("last_updated")
    ttl1 = Int("ttl1")  # original TTL
    ttl2 = Int("ttl2")  # higher TTL

    def is_expired(ttl):
        return last_updated + ttl * 86400 < now

    s.add(ttl1 > 0)
    s.add(ttl2 > ttl1)   # ttl2 is strictly larger
    s.add(now > 0)
    s.add(last_updated >= 0)

    # Centroid is retained under ttl1 (not expired)
    s.add(Not(is_expired(ttl1)))

    # Claim: it must also be retained under ttl2.
    s.add(is_expired(ttl2))
    assert s.check() == unsat, "Raising TTL must not expire a previously retained centroid"


def test_lowering_ttl_never_retains_expired_centroid():
    """Decreasing TTL_DAYS cannot cause an already-expired centroid to be retained."""
    s = Solver()

    now = Int("now")
    last_updated = Int("last_updated")
    ttl1 = Int("ttl1")  # original TTL
    ttl2 = Int("ttl2")  # lower TTL

    def is_expired(ttl):
        return last_updated + ttl * 86400 < now

    s.add(ttl1 > 0)
    s.add(ttl2 > 0)
    s.add(ttl2 < ttl1)   # ttl2 is strictly smaller
    s.add(now > 0)
    s.add(last_updated >= 0)

    # Centroid is expired under ttl1
    s.add(is_expired(ttl1))

    # Claim: it must also be expired under ttl2.
    s.add(Not(is_expired(ttl2)))
    assert s.check() == unsat, "Lowering TTL must not retain an already-expired centroid"


def test_default_ttl_covers_90_days():
    """With default TTL=90 days, a centroid untouched for 91 days is expired."""
    s = Solver()

    now = Int("now")
    last_updated = Int("last_updated")
    ttl_days = 90

    ttl_seconds = ttl_days * 86400
    is_expired = last_updated + ttl_seconds < now

    s.add(now == 1_000_000_000)
    s.add(last_updated == now - 91 * 86400)  # 91 days ago

    s.add(Not(is_expired))
    assert s.check() == unsat, "Centroid untouched for 91 days must be expired with TTL=90"


def test_centroid_touched_yesterday_retained():
    """A centroid updated 1 day ago is retained under default TTL=90."""
    s = Solver()

    now = Int("now")
    last_updated = Int("last_updated")
    ttl_days = 90

    ttl_seconds = ttl_days * 86400
    is_expired = last_updated + ttl_seconds < now

    s.add(now == 1_000_000_000)
    s.add(last_updated == now - 86400)  # 1 day ago

    s.add(is_expired)
    assert s.check() == unsat, "Centroid updated 1 day ago must NOT be expired under TTL=90"
