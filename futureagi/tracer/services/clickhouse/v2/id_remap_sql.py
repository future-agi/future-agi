"""id_remap_sql — the ONE canonical SQL fragment that resolves a span's
``end_user_id`` (or ``trace_session_id``) through the P3b id-remap, so a
cross-cutover *straddler* (an identity with both old random-uuid4 spans AND new
deterministic-UUIDv5 spans) AND a *consolidation group* (many old ids that the
deterministic id collapses onto ONE new id) each read as ONE entity — counted
once, never split, never double-counted.

WHY (DESIGN §3 / §3.1 / §10.1, schema ``019_id_remap.sql``). P3b re-keys the
curated ``end_users`` / ``trace_sessions`` surrogate id from a random PG ``uuid4``
to a deterministic ``UUIDv5`` so ingestion can drop the hot-path ``get_or_create``.
Old ids stay the CURATED key; the ``end_user_id_remap (old_id, new_id)`` /
``trace_session_id_remap`` bridge maps a span back to its curated id at read time
(instead of rewriting billions of span rows). This module wires the PER-USER /
PER-SESSION reads to that bridge.

THE MAP IS MANY-TO-ONE (this is the whole subtlety). The deterministic id
*consolidates* duplicates that the PG unique constraint missed: NULL-``user_id_type``
enduser dupes (879→544 on box data) and rename-bug duplicate sessions (1405→1404).
So for a consolidation group SEVERAL ``old_id``s share ONE ``new_id``:

    (old_A, new_X), (old_B, new_X)        ← two remap rows, one new_id

THE FAN-OUT BUG THIS MODULE FIXES (P3B_ACCEPTANCE_MATRIX gate C2). The naive
resolution was ``LEFT JOIN end_user_id_remap FINAL ON span.end_user_id = new_id``
+ ``coalesce-to-old_id``. ``FINAL`` dedups the RMT by ``old_id`` (its ORDER BY),
so BOTH ``(old_A, new_X)`` and ``(old_B, new_X)`` survive. A post-flip span
carrying ``new_X`` then matches BOTH rows → the single span row FANS OUT into two
(one resolving to ``old_A``, one to ``old_B``) → the user SPLITS and the post-flip
span is DOUBLE-COUNTED. Proven on ch_rehearsal: ``old_A 100tok + old_B 50 +
new_X 300 = 450`` truth read as TWO rows ``400 + 350 = 750``. (A 1:1 straddler —
sarthak, ``old 3b35cb40 → new 65e4ebd8`` — never fanned out and read 514
correctly; the bug is invisible until a group is many-to-one.)

THE FIX — SURVIVOR COLLAPSE BEFORE GROUP BY. Resolution maps EVERY id in a group
— each ``old_id`` AND the shared ``new_id`` — to ONE canonical **survivor** old id,
so the group's old+new spans collapse to a single grouping key with NO fan-out:

    survivor(group) = argMin(old_id, toString(old_id))   -- per new_id

i.e. the lexicographically-smallest CANONICAL UUID STRING among the group's old
ids. (NOT ``min(old_id)``: ClickHouse's native UUID comparison is byte-swapped and
does NOT match string/integer order — verified — so a plain ``min`` would pick an
id no other tool can reproduce. The string-argMin is reproducible verbatim in
Python as ``min(olds, key=str)``.) The survivor is always an OLD id because the
curated ``end_users`` / dict is still keyed by old ids in the dual-source window —
labels (``dictGet``) only resolve for an old key.

``remap_left_join`` therefore joins to a derived **survivor map** (``any_id ->
survivor_id``) instead of the raw remap table. The map emits one row per old id
(window arm) and one per new id (group arm), then an OUTER ``GROUP BY any_id``
guarantees ONE row per id — so a span's stored id matches AT MOST ONE map row and
fan-out is structurally impossible. (The dedup is normally a no-op: old-id space
∩ new-id space = ∅, uuid4 vs uuid5. It is load-bearing ONLY for the Session §3.1
carve-out's identity remap rows ``old_id == new_id`` — see ``_survivor_map_subquery``.)
Properties this preserves/gains:
  • Pre-flip, all spans carry old ids; a non-consolidated old maps to ITSELF
    (argMin of a singleton group), so the join is a byte-identical no-op on
    1:1 data (acceptance gate B; the harness island has no consolidation groups).
  • A 1:1 straddler's new-id span resolves to its single old → reads as one user
    (gate C, 514) — unchanged.
  • A consolidation group's old_A/old_B/new_X ALL resolve to the survivor → ONE
    row, every span counted once (gate C2, 450). On the box this also folds the
    group pre-flip — that IS the intended consolidation (§3.1), surfaced at read
    time before the step3 sweep makes it physical (expand→contract).
  • Per-span reads (span_list) that previously fanned a post-flip span across the
    duplicate remap rows now collapse it too — same win, for free.

THE STEP3 SURVIVOR-KEY CONTRACT (load-bearing — read before building the sweep).
This read encodes ``survivor key = argMin lowercase-string old per new_id``. The
step3 consolidation sweep (gate D) physically collapses each group's curated rows
to ONE survivor row and rekeys ``span_user_rollup``. It MUST key that survivor
row by the IDENTICAL rule, and MUST **prefer-live** (a soft-deleted row must never
become the survivor key while a live sibling exists). If step3 instead keyed the
survivor by "latest version" (or any other rule), this read would resolve spans
to an id the sweep deleted → ``filtered_end_users`` (``is_deleted=0``) would not
contain it → the user would VANISH from every read, permanently. The rule is
deliberately DECOUPLED: the survivor **KEY** (which old id the consolidated row
uses) is this shared, remap-derivable expression; the survivor **FIELDS** (which
row's ``user_id`` / ``metadata`` win) are step3's "latest version" choice, written
INTO the survivor-key row. Reproduce the key as ``min(olds, key=str)`` in Python,
preferring ``deleted=False`` first. (A more drift-proof option for step3 is to
materialize ``survivor_id`` into the remap once, so read and sweep read one stored
value — see P3B_ACCEPTANCE_MATRIX gate C2 / D.)

LIMITATION (current data is safe). Because the survivor is chosen by string order,
not liveness, a group whose argMin-string survivor is soft-deleted while a sibling
is live would drop the live user from the list. Verified on box data (pg-full):
endusers have 0 soft-deletes and 0 mixed-deletion consolidation groups (28 strict
/ 29 loose multi-row groups), so this cannot manifest today; sessions were not
loaded on pg-full and are unverified. The prefer-live survivor (which needs the
entity table joined for ``is_deleted``) is deferred to step3 per the contract
above rather than complicating this generic, entity-agnostic helper now.

THE UNMATCHED-JOIN NULL GOTCHA (unchanged from the original design). ClickHouse
fills the UNMATCHED side of a LEFT JOIN with the column-type DEFAULT, not NULL
(unless ``join_use_nulls=1``). ``survivor_id`` is a non-nullable ``UUID``, so a
span whose id is in NO map row (never remapped) comes back as the ZERO-UUID
(``00000000-…``), NOT NULL. We guard on the zero-uuid explicitly (``if survivor is
NULL or zero-uuid → use the span's own id``) rather than a query-wide
``join_use_nulls`` flag (which would flip NULL semantics of the OTHER joins in
these read queries and risk the gate-B byte-identical guarantee). The zero-uuid
can never be a legitimate ``old_id`` (it is a real PG ``uuid4``), so the guard is
exact. The ``isNull`` arm is harmless belt-and-suspenders for a future caller that
does run under ``join_use_nulls=1``.
"""

from __future__ import annotations

# ClickHouse zero-value for a non-nullable UUID column — the value CH fills into
# the UNMATCHED side of a LEFT JOIN (see module docstring). Defined locally (NOT
# imported from query_builders.base) to keep this leaf module import-cycle-free:
# the query_builders package's __init__ imports the builders, which import THIS
# module, so a back-import would be circular. Same literal as base.NIL_UUID /
# adapter — asserted identical by the unit tests.
NIL_UUID = "00000000-0000-0000-0000-000000000000"

# Default join-side alias for the survivor map (callers may pass their own; two
# remaps on one row — eu + ts — MUST use distinct aliases, e.g. eu_remap/ts_remap).
REMAP_ALIAS = "id_remap"


def _survivor_map_subquery(remap_table: str) -> str:
    """The derived ``(any_id -> survivor_id)`` survivor map for ``remap_table``.

    Two arms, UNION ALL'd, then an OUTER ``GROUP BY any_id`` that guarantees one
    row per id:

      • window arm — each ``old_id`` → its group's survivor (the argMin runs over
        the ``PARTITION BY new_id`` window, so EVERY old row carries the group's
        survivor, folding non-survivor olds onto the survivor);
      • group arm — each ``new_id`` → its group's survivor (so a post-flip
        new-id span resolves to the same survivor as the group's old-id spans).

    THE OUTER ``GROUP BY any_id`` (the carve-out fan-out guard). For a normal
    consolidation/straddler the two arms' keys are disjoint (an ``old_id`` is a
    random uuid4, a ``new_id`` a deterministic uuid5 — they never collide), so
    every ``any_id`` is already unique and the GROUP BY is a no-op. BUT the
    Session §3.1 carve-out (a renamed session whose external id is unrecoverable)
    is represented as an IDENTITY remap row ``(old_id == new_id == K)`` — both
    arms then emit ``any_id = K``, and WITHOUT the dedup a span carrying ``K``
    would match BOTH in the LEFT JOIN and fan out (the very gate-C2 double-count
    this module exists to kill, reintroduced). The two emitted rows carry the
    SAME survivor (both argMin over the singleton group ``new_id = K`` → ``K``),
    so ``min(survivor_id)`` collapses them deterministically to one ``(K, K)``
    row (a carve-out resolves to itself — correct). The dedup makes the map
    fan-out-safe regardless of whether step3 represents carve-outs as identity
    rows or as the ABSENCE of a remap row (a span with no match already resolves
    to itself); see the step3 contract in the module docstring.

    ``FINAL`` collapses the ReplacingMergeTree (one row per ``old_id``) so a
    re-run of the build reads identically. The survivor selector is
    ``argMin(old_id, toString(old_id))`` — the lexicographically-smallest
    canonical UUID string of the group's old ids (see module docstring on why
    NOT ``min``); ``min(olds, key=str)`` reproduces it in Python (step3 contract).
    """
    return (
        "SELECT any_id, min(survivor_id) AS survivor_id FROM ("
        "SELECT old_id AS any_id, "
        "argMin(old_id, toString(old_id)) OVER (PARTITION BY new_id) AS survivor_id "
        f"FROM {remap_table} FINAL "
        "UNION ALL "
        "SELECT new_id AS any_id, "
        "argMin(old_id, toString(old_id)) AS survivor_id "
        f"FROM {remap_table} FINAL "
        "GROUP BY new_id"
        ") GROUP BY any_id"
    )


def resolved_id_expr(span_id_col: str, remap_alias: str = REMAP_ALIAS) -> str:
    """Return the SQL expression that yields the *resolved* (survivor) id for
    ``span_id_col``.

    ``span_id_col`` is the (qualified) span column being resolved, e.g.
    ``s.end_user_id``; ``remap_alias`` is the alias the survivor map was joined
    under (the join condition is ``<span_id_col> = <remap_alias>.any_id``, emitted
    by :func:`remap_left_join`).

    Resolves to the map's ``survivor_id`` (the canonical old id for the span's
    consolidation group / straddler) when the span's id is in the map, else the
    span's own id. The fallback path is the zero-uuid guard described in the
    module docstring (unmatched LEFT JOIN → zero-uuid, not NULL). The ``isNull``
    arm is belt-and-suspenders for ``join_use_nulls=1``.
    """
    surv = f"{remap_alias}.survivor_id"
    return f"if({surv} IS NULL OR {surv} = toUUID('{NIL_UUID}'), {span_id_col}, {surv})"


def remap_left_join(
    span_id_col: str, remap_table: str, remap_alias: str = REMAP_ALIAS
) -> str:
    """Return the ``LEFT JOIN (<survivor map>) AS <alias> ON …`` clause that pairs
    with :func:`resolved_id_expr`.

    Joins to the derived survivor map (:func:`_survivor_map_subquery`), NOT the raw
    remap table, so a consolidation group's many old ids + shared new id all map to
    ONE survivor and a post-flip span can match AT MOST ONE map row — eliminating
    the gate-C2 fan-out (see module docstring). ``remap_table`` is unqualified
    (``end_user_id_remap`` / ``trace_session_id_remap``) so the connection's
    configured database (``CH25_DATABASE`` / ``CH_DATABASE``) is the single
    dev/test/prod switch — same DB-agnostic rule as the schema files.
    """
    return (
        f"LEFT JOIN ({_survivor_map_subquery(remap_table)}) AS {remap_alias} "
        f"ON {span_id_col} = {remap_alias}.any_id"
    )


__all__ = ["REMAP_ALIAS", "resolved_id_expr", "remap_left_join"]
