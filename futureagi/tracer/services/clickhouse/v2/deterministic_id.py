"""deterministic_id — pure UUIDv5 surrogate-id functions for the CURATED
``end_users`` / ``trace_sessions`` dimensions (CH-derived-dimensions, DESIGN §3).

WHY THIS EXISTS. Today an ``EndUser`` / ``TraceSession`` surrogate id is a random
PG ``uuid4`` minted by the ingest-hot-path ``get_or_create`` (one PG round-trip per
trace). P3b moves the id to a DETERMINISTIC function of the natural key so the
collector can compute it from data already on the span and DROP the hot-path
``get_or_create`` entirely — lookup-free AND race-free (same identity → same id on
every collector, no central allocator, no two-collectors-mint-different-ids race).

THE CONTRACT — THE BYTES ARE FROZEN. These functions define the id that BOTH the
historical remap (``ch25_build_id_remap``) AND the future ingestion path (P3b
step2 — ``trace_ingestion`` / ``create_otel_span`` / ``langfuse_upsert``) MUST
produce. A historical id and a new ingest-time id for the *same* identity only
collide if the namespace seeds and the key string are BYTE-IDENTICAL on both
sides. So:

  • The namespace seeds (``"futureagi.enduser.v1"`` / ``"futureagi.session.v1"``)
    and the key-string layout below are LOAD-BEARING. Do NOT change a seed, a
    separator, a field order, or the NULL sentinel without re-keying every
    historical row in lockstep (a breaking, full-remap migration). The ``v1`` in
    each seed is the version handle for exactly that event.
  • Future ingestion MUST call THESE functions (never re-derive the formula
    inline) so the two paths can never drift. This module is the single source of
    truth for the formula.

THE NULL SENTINEL (load-bearing). ``user_id_type`` is NULL on ~85% of prod rows
(the SDK derives it only when it can normalize the external id). The key renders a
NULL/empty type as the ``""`` sentinel via ``user_id_type or ""`` — NOT
``str(user_id_type)`` (which would render ``None`` → the literal ``"None"``). This
exact sentinel is what makes the NULL-type duplicates consolidate onto one id (the
validated box dry-run collapsed 879 endusers → 544 distinct ids precisely because
NULL and any later ``""`` for the same ``(project, org, user_id)`` map together).
Coercion rule (mirrors PG's text form, matches the dry-run): the UUID/scalar
fields are ``str()``-coerced (a ``uuid.UUID`` → its canonical lowercase form, the
same string PG stores); ``user_id_type`` is sentinel-mapped BEFORE any ``str()`` so
``None`` becomes ``""``, never ``"None"``.

NO I/O. Pure functions — no DB, no settings, no logging. Trivially unit-testable
and safe to call on the ingest hot path. The 879→544 / 1405→1404 consolidation is
an emergent property of applying these to the box data; this module does NOT model
the §3.1 carve-out / survivor-row / span-remap logic (that is P3b step3, the
consolidation sweep) — it computes the uniform id for one identity only.

FORMULA (validated by the read-only box dry-run — 879 endusers → 544 distinct ids,
1405 sessions → 1404 distinct ids):

    NS_ENDUSER = uuid5(NAMESPACE_DNS, "futureagi.enduser.v1")
    NS_SESSION = uuid5(NAMESPACE_DNS, "futureagi.session.v1")
    end_user_id      = uuid5(NS_ENDUSER, f"{project_id}|{organization_id}|{user_id}|{user_id_type or ''}")
    trace_session_id = uuid5(NS_SESSION, f"{project_id}|{name}")
"""

from __future__ import annotations

import uuid

# ─── Namespace constants (frozen) ───────────────────────────────────────────
# Seeded off NAMESPACE_DNS so the two entity namespaces are themselves stable,
# reproducible UUIDv5 values (no random/hand-picked UUID literal to transcribe).
# The seed string IS the contract — see the module docstring. Pinned golden
# values (assert these never drift): NS_ENDUSER = 97daafcc-ae7b-5a44-a76b-
# c85e63059e1c, NS_SESSION = 1c4977df-2af9-5330-b34f-7969ffdabf25.
NS_ENDUSER: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_DNS, "futureagi.enduser.v1")
NS_SESSION: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_DNS, "futureagi.session.v1")


def deterministic_end_user_id(
    project_id,
    organization_id,
    user_id,
    user_id_type,
) -> uuid.UUID:
    """Deterministic ``end_user_id`` for an EndUser identity (DESIGN §3 / §3.1).

    Natural key: ``(project_id, organization_id, user_id, user_id_type)`` — the
    same tuple as PG ``tracer_enduser``'s ``unique_together``. ``user_id_type`` is
    NULL on most rows; it is mapped to the ``""`` sentinel via ``or ""`` so a NULL
    and a later ``""`` for the same ``(project, org, user_id)`` resolve to the SAME
    id (the consolidation mechanism — 879 → 544 on box data).

    All inputs are ``str()``-coerced into the key string so a ``uuid.UUID`` and its
    canonical-string form (and PG's text form) all yield the same id. The
    sentinel is applied to ``user_id_type`` BEFORE coercion, so ``None`` becomes
    ``""`` — never the literal ``"None"`` (a blanket ``str(None)`` would break the
    frozen byte contract).

    Pure: no I/O. Future ingestion (P3b step2) MUST call this exact function.
    """
    type_token = user_id_type or ""  # NULL/empty → "" sentinel (NOT "None")
    key = f"{project_id}|{organization_id}|{user_id}|{type_token}"
    return uuid.uuid5(NS_ENDUSER, key)


def deterministic_trace_session_id(project_id, name) -> uuid.UUID:
    """Deterministic ``trace_session_id`` for a TraceSession identity (DESIGN §3).

    Natural key: ``(project_id, name)`` — where ``name`` is the external session
    id (PG ``trace_session``'s ``(project, name)`` dedup key). Inputs are
    ``str()``-coerced into the key string (a ``uuid.UUID`` project_id → its
    canonical form). A NULL ``name`` renders as the literal ``"None"`` here, which
    is acceptable: the box-data validation (1405 → 1404) ran on this exact formula,
    and the §3.1 rename carve-out (renamed sessions keep their old random id) is a
    P3b-step3 concern handled in the consolidation sweep, NOT in this uniform id.

    Pure: no I/O. Future ingestion (P3b step2) MUST call this exact function.
    """
    key = f"{project_id}|{name}"
    return uuid.uuid5(NS_SESSION, key)


__all__ = [
    "NS_ENDUSER",
    "NS_SESSION",
    "deterministic_end_user_id",
    "deterministic_trace_session_id",
]
