"""id_remap_sql — resolve a span's ``end_user_id`` / ``trace_session_id`` through
the P3b id-remap so a cross-cutover straddler AND a many-old→one-new consolidation
group each read as ONE entity (counted once, never split or double-counted).

Background. P3b re-keys the curated ``end_users`` / ``trace_sessions`` id from a
random PG ``uuid4`` to a deterministic ``UUIDv5`` (DESIGN §3); old ids stay the
curated key and the ``*_id_remap (old_id, new_id)`` bridge maps a span back at read
time. The map is MANY-TO-ONE — the deterministic id consolidates dupes the PG
unique constraint missed (NULL-type endusers 879→544, rename-bug sessions
1405→1404) — so several ``old_id``s can share one ``new_id``.

The bug this fixes (gate C2). A naive ``JOIN remap ON span = new_id`` lets a
post-flip ``new_X`` span match BOTH ``(old_A,new_X)`` and ``(old_B,new_X)`` (both
survive ``FINAL``) → fan-out → split user + double-count (450 truth read as 750).

The fix. Resolve via a derived SURVIVOR MAP that maps EVERY id (each old AND the
shared new) to ONE survivor old per ``new_id`` = ``argMin(old_id, toString(old_id))``
— the lexicographically-smallest UUID *string* (NOT ``min(uuid)``: CH's UUID order
is byte-swapped; the string form equals Python ``min(olds, key=str)``, so the build
side can reproduce it). The survivor is an OLD id because the curated table / dict
stays old-keyed in the dual-source window. Pre-flip every old maps to itself → a
byte-identical no-op (gate B).

Zero-uuid gotcha. CH fills an unmatched LEFT-JOIN UUID column with the zero-uuid,
not NULL, so ``resolved_id_expr`` guards ``survivor IS NULL OR == zero-uuid → span's
own id`` (a real ``uuid4`` is never the zero-uuid).

Step3 survivor-key / prefer-live liveness contract and the deferred limitation are
documented in P3B_ACCEPTANCE_MATRIX C2/D, REMAP_RETIREMENT.md, and
STEP2_REVIEW_FOLLOWUPS.md.
"""

from __future__ import annotations

# CH zero-value for a non-nullable UUID (the unmatched-LEFT-JOIN fill). Local copy,
# not imported from query_builders.base, to keep this leaf module import-cycle-free.
NIL_UUID = "00000000-0000-0000-0000-000000000000"

# Default join alias. A dual eu+ts read on one row MUST pass distinct aliases
# (e.g. eu_remap / ts_remap) so the two survivor-map joins don't collide.
REMAP_ALIAS = "id_remap"


def survivor_map_subquery(remap_table: str) -> str:
    """Derived ``(any_id -> survivor_id)`` map for ``remap_table``.

    Window arm maps each ``old_id``, group arm each ``new_id``, to its group's
    survivor = ``argMin(old_id, toString(old_id))`` per ``new_id``. The outer
    ``GROUP BY any_id`` dedups the Session §3.1 carve-out identity row
    (``old_id == new_id``, emitted by both arms) so a span matches at most one row
    — no fan-out. ``FINAL`` collapses the RMT. See the module docstring.
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
    """SQL for the resolved (survivor) id of ``span_id_col``: the joined map's
    ``survivor_id``, else the span's own id (zero-uuid/NULL guard — see module
    docstring). ``remap_alias`` must match the alias :func:`remap_left_join` used.
    """
    surv = f"{remap_alias}.survivor_id"
    return f"if({surv} IS NULL OR {surv} = toUUID('{NIL_UUID}'), {span_id_col}, {surv})"


def remap_left_join(
    span_id_col: str, remap_table: str, remap_alias: str = REMAP_ALIAS
) -> str:
    """``LEFT JOIN (<survivor map>) AS <alias> ON <span_id_col> = <alias>.any_id``
    — pairs with :func:`resolved_id_expr`. ``remap_table`` is unqualified so
    ``CH25_DATABASE`` is the single dev/test/prod switch.
    """
    return (
        f"LEFT JOIN ({survivor_map_subquery(remap_table)}) AS {remap_alias} "
        f"ON {span_id_col} = {remap_alias}.any_id"
    )


__all__ = [
    "REMAP_ALIAS",
    "resolved_id_expr",
    "remap_left_join",
    "survivor_map_subquery",
]
