"""Everything the Ask page draws for an Instant eval run, aggregated in SQL.

The run's rows and their facets live in ``tracer_instant_eval_row``; each
row's answer to each question is its live ``EvalLogger`` entry (one per row,
question and task). ``summary`` joins the two once and groups the result by
time bucket, user, model, turns and error in one statement, so a run of
100,000 rows costs the same few queries as a run of 100. Python only reshapes
the grouped counts: hours into the viewer's timeline bins, days and weekdays.

``rows_page`` and ``iter_export`` read the rows themselves, filtered the way
the charts select them (an answer, a time range, a group).

A user question's rows are each user's latest sessions, every one answered.
``_user_rollup`` makes one row of each user, with one answer per question,
and everything a user question counts, lists, filters or exports reads it:
a user is counted once, under one answer.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import re
import uuid
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

logger = structlog.get_logger(__name__)

ERROR = "__error__"
SKIPPED = "__skipped__"
PENDING = "__pending__"
FAILED_LABELS = (ERROR, SKIPPED)
UNSURE_BELOW = 0.6
MAX_BINS = 120
TOP_USERS = 50
RATE_WINDOW_S = 120

# Timeline bin sizes, smallest first; the first one that fits MAX_BINS wins.
_MIN = 60_000
_HOUR = 60 * _MIN
_DAY = 24 * _HOUR
BIN_LADDER = (
    _MIN,
    5 * _MIN,
    15 * _MIN,
    30 * _MIN,
    _HOUR,
    3 * _HOUR,
    6 * _HOUR,
    12 * _HOUR,
    _DAY,
    7 * _DAY,
)

# The EvalLogger column that names a row, as text, per row type.
RID = {
    "traces": "l.trace_id::text",
    "sessions": "l.trace_session_id::text",
    "spans": "l.observation_span_id",
}

# One label per entry: the answer, or what kept it from having one.
LABEL = f"""CASE
  WHEN l.status IN ('pending', 'running') THEN '{PENDING}'
  WHEN l.error THEN '{ERROR}'
  WHEN COALESCE(l.skipped_reason, '') <> '' THEN '{SKIPPED}'
  WHEN jsonb_typeof(l.output_str_list) = 'array'
       AND jsonb_array_length(l.output_str_list) > 0 THEN l.output_str_list->>0
  WHEN substring(l.output_str from '"choice"\\s*:\\s*"([^"]*)"') IS NOT NULL
       THEN substring(l.output_str from '"choice"\\s*:\\s*"([^"]*)"')
  WHEN COALESCE(l.output_str, '') <> '' AND left(ltrim(l.output_str), 1) <> '{{'
       THEN l.output_str
  ELSE '{ERROR}' END"""

CONFIDENCE = """CASE WHEN jsonb_typeof(l.output_metadata->'jev'->'confidence') = 'number'
  THEN (l.output_metadata->'jev'->>'confidence')::float END"""

TURN_BUCKET = """CASE WHEN turns IS NULL THEN NULL WHEN turns <= 1 THEN '1'
  WHEN turns <= 3 THEN '2-3' WHEN turns <= 6 THEN '4-6' ELSE '7+' END"""
TURN_KEYS = ("1", "2-3", "4-6", "7+")
# The traced app's own error, not Jev's.
ERROR_GROUP_LABEL = "Trace had an error"
TURN_LABELS = {"1": "1 turn", "2-3": "2–3 turns", "4-6": "4–6 turns", "7+": "7+ turns"}
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class BadFilter(ValueError):
    pass


# tzdata "backward" links that browsers still report (Chrome sends
# Asia/Calcutta even when the machine is set to Asia/Kolkata) but that Postgres
# on current Debian images, built on the system tzdata without tzdata-legacy,
# does not know. Python's ZoneInfo accepts them, so they are mapped to their
# canonical names before any reaches SQL.
LEGACY_ZONES = {
    "Africa/Asmera": "Africa/Nairobi",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "America/Catamarca": "America/Argentina/Catamarca",
    "America/Coral_Harbour": "America/Panama",
    "America/Cordoba": "America/Argentina/Cordoba",
    "America/Fort_Wayne": "America/Indiana/Indianapolis",
    "America/Godthab": "America/Nuuk",
    "America/Indianapolis": "America/Indiana/Indianapolis",
    "America/Jujuy": "America/Argentina/Jujuy",
    "America/Knox_IN": "America/Indiana/Knox",
    "America/Louisville": "America/Kentucky/Louisville",
    "America/Mendoza": "America/Argentina/Mendoza",
    "America/Porto_Acre": "America/Rio_Branco",
    "America/Rosario": "America/Argentina/Cordoba",
    "America/Santa_Isabel": "America/Tijuana",
    "America/Shiprock": "America/Denver",
    "America/Virgin": "America/St_Thomas",
    "Antarctica/South_Pole": "Antarctica/McMurdo",
    "Asia/Ashkhabad": "Asia/Ashgabat",
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Chongqing": "Asia/Shanghai",
    "Asia/Chungking": "Asia/Shanghai",
    "Asia/Dacca": "Asia/Dhaka",
    "Asia/Harbin": "Asia/Shanghai",
    "Asia/Istanbul": "Europe/Istanbul",
    "Asia/Kashgar": "Asia/Urumqi",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Macao": "Asia/Macau",
    "Asia/Rangoon": "Asia/Yangon",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Tel_Aviv": "Asia/Jerusalem",
    "Asia/Thimbu": "Asia/Thimphu",
    "Asia/Ujung_Pandang": "Asia/Makassar",
    "Asia/Ulan_Bator": "Asia/Ulaanbaatar",
    "Atlantic/Faeroe": "Atlantic/Faroe",
    "Atlantic/Jan_Mayen": "Europe/Oslo",
    "Australia/ACT": "Australia/Sydney",
    "Australia/Canberra": "Australia/Sydney",
    "Australia/LHI": "Australia/Lord_Howe",
    "Australia/NSW": "Australia/Sydney",
    "Australia/North": "Australia/Darwin",
    "Australia/Queensland": "Australia/Brisbane",
    "Australia/South": "Australia/Adelaide",
    "Australia/Tasmania": "Australia/Hobart",
    "Australia/Victoria": "Australia/Melbourne",
    "Australia/West": "Australia/Perth",
    "Australia/Yancowinna": "Australia/Broken_Hill",
    "Brazil/Acre": "America/Rio_Branco",
    "Brazil/DeNoronha": "America/Noronha",
    "Brazil/East": "America/Sao_Paulo",
    "Brazil/West": "America/Manaus",
    "Canada/Atlantic": "America/Halifax",
    "Canada/Central": "America/Winnipeg",
    "Canada/Eastern": "America/Toronto",
    "Canada/Mountain": "America/Edmonton",
    "Canada/Newfoundland": "America/St_Johns",
    "Canada/Pacific": "America/Vancouver",
    "Canada/Saskatchewan": "America/Regina",
    "Canada/Yukon": "America/Whitehorse",
    "Chile/Continental": "America/Santiago",
    "Chile/EasterIsland": "Pacific/Easter",
    "Cuba": "America/Havana",
    "Egypt": "Africa/Cairo",
    "Eire": "Europe/Dublin",
    "Europe/Belfast": "Europe/London",
    "Europe/Kiev": "Europe/Kyiv",
    "Europe/Nicosia": "Asia/Nicosia",
    "Europe/Tiraspol": "Europe/Chisinau",
    "Europe/Uzhgorod": "Europe/Kyiv",
    "Europe/Zaporozhye": "Europe/Kyiv",
    "GB": "Europe/London",
    "GB-Eire": "Europe/London",
    "Greenwich": "Etc/GMT",
    "Hongkong": "Asia/Hong_Kong",
    "Iceland": "Atlantic/Reykjavik",
    "Iran": "Asia/Tehran",
    "Israel": "Asia/Jerusalem",
    "Jamaica": "America/Jamaica",
    "Japan": "Asia/Tokyo",
    "Kwajalein": "Pacific/Kwajalein",
    "Libya": "Africa/Tripoli",
    "Mexico/BajaNorte": "America/Tijuana",
    "Mexico/BajaSur": "America/Mazatlan",
    "Mexico/General": "America/Mexico_City",
    "NZ": "Pacific/Auckland",
    "NZ-CHAT": "Pacific/Chatham",
    "Navajo": "America/Denver",
    "PRC": "Asia/Shanghai",
    "Pacific/Enderbury": "Pacific/Kanton",
    "Pacific/Johnston": "Pacific/Honolulu",
    "Pacific/Ponape": "Pacific/Pohnpei",
    "Pacific/Samoa": "Pacific/Pago_Pago",
    "Pacific/Truk": "Pacific/Chuuk",
    "Pacific/Yap": "Pacific/Chuuk",
    "Poland": "Europe/Warsaw",
    "Portugal": "Europe/Lisbon",
    "ROC": "Asia/Taipei",
    "ROK": "Asia/Seoul",
    "Singapore": "Asia/Singapore",
    "Turkey": "Europe/Istanbul",
    "UCT": "Etc/UTC",
    "US/Alaska": "America/Anchorage",
    "US/Aleutian": "America/Adak",
    "US/Arizona": "America/Phoenix",
    "US/Central": "America/Chicago",
    "US/East-Indiana": "America/Indiana/Indianapolis",
    "US/Eastern": "America/New_York",
    "US/Hawaii": "Pacific/Honolulu",
    "US/Indiana-Starke": "America/Indiana/Knox",
    "US/Michigan": "America/Detroit",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
    "US/Samoa": "Pacific/Pago_Pago",
    "Universal": "Etc/UTC",
    "W-SU": "Europe/Moscow",
    "Zulu": "Etc/UTC",
}


@functools.lru_cache(maxsize=512)
def _postgres_has_zone(name: str) -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_timezone_names WHERE name = %s", [name])
        return cur.fetchone() is not None


def _postgres_knows(name: str) -> bool:
    """Whether Postgres can convert to ``name`` (its tzdata may lack names
    Python's has). An unanswerable check lets the name through, uncached."""
    try:
        return _postgres_has_zone(name)
    except Exception:
        return True


def zone(name: str | None) -> ZoneInfo:
    """The browser's time zone, by a name Postgres knows: legacy links map to
    their canonical zone, and a name Postgres can't convert to is a 400, not
    a summary that fails on every read."""
    name = name or "UTC"
    name = LEGACY_ZONES.get(name, name)
    try:
        tz = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise BadFilter(f"Unknown time zone: {name}") from None
    if not _postgres_knows(tz.key):
        raise BadFilter(f"Unknown time zone: {name}")
    return tz


@dataclass
class RunView:
    """The run being read: its task, unit, questions and window."""

    task_id: str
    row_type: str
    unit: str
    config_ids: list[str]
    tz: ZoneInfo
    date_from: datetime | None = None
    date_to: datetime | None = None
    cancelled: bool = False
    labels: dict[str, list[str]] = field(default_factory=dict)
    # The rows a run stores once they are all in, and the version that
    # changes when it reads more: lets the rows' span and users be kept.
    rows_expected: int | None = None
    rows_version: int = 0
    # Whether answers are still landing.
    live: bool = False

    @property
    def by_user(self) -> bool:
        return self.unit == "users"


def _rows(sql: str, params: dict) -> list[tuple]:
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _count(cond: str = "") -> str:
    return f"count(*) FILTER (WHERE {cond})" if cond else "count(*)"


# ─── Time ────────────────────────────────────────────────────────────────────
def pick_bin(span_ms: float) -> int:
    # Bins start on local boundaries, so the first one may begin before the
    # window does: leave room for it.
    for size in BIN_LADDER:
        if span_ms / size <= MAX_BINS - 1:
            return size
    return BIN_LADDER[-1]


def _hour_label(hour: int) -> str:
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def _hour_range(start: int, end: int) -> str:
    """ "6–9 AM", "9 AM–12 PM"."""
    a, b = start % 24, end % 24
    if b in (0, 12):
        return f"{_hour_label(a)}–{_hour_label(b)}"
    return f"{a % 12 or 12}–{_hour_label(b)}"


def _day_label(ts: datetime) -> str:
    return f"{ts:%b} {ts.day}"


def _local_ms(local: datetime, tz: ZoneInfo) -> int:
    return int(local.replace(tzinfo=tz).timestamp() * 1000)


def _bin_start(local: datetime, bin_ms: int, origin: datetime) -> datetime:
    """The start of the bin a naive local time falls in, on local boundaries."""
    if bin_ms >= 7 * _DAY:
        days = (local.date() - origin.date()).days
        return origin + timedelta(days=days - days % 7)
    if bin_ms >= _DAY:
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    if bin_ms >= _HOUR:
        k = bin_ms // _HOUR
        return local.replace(
            hour=local.hour - local.hour % k, minute=0, second=0, microsecond=0
        )
    k = bin_ms // _MIN
    return local.replace(
        minute=local.minute - local.minute % k, second=0, microsecond=0
    )


def _step(local: datetime, bin_ms: int) -> datetime:
    """The next bin start, stepping local wall-clock time."""
    if bin_ms >= _DAY:
        return local + timedelta(days=bin_ms // _DAY)
    return local + timedelta(milliseconds=bin_ms)


def _bucket_sql(bin_ms: int) -> tuple[str, str]:
    """The SQL time bucket every grouping starts from: one local hour, or the
    bin itself when bins are shorter than an hour."""
    if bin_ms >= _HOUR:
        return "date_trunc('hour', r.time AT TIME ZONE %(tz)s)", ""
    return (
        "date_bin(%(grain)s::interval, r.time AT TIME ZONE %(tz)s, TIMESTAMP '2000-01-03')",
        f"{bin_ms // _MIN} minutes",
    )


# ─── Summary ─────────────────────────────────────────────────────────────────
def _window(view: RunView) -> tuple[datetime | None, datetime | None, int, int, list]:
    """The rows' first and last time, how many rows and users there are, and
    the busiest users. Kept once every row is stored: rows are written once,
    at the run's first step, and again only when it reads more."""
    key = (
        f"ie-window:{view.task_id}:{view.rows_version}:{view.rows_expected}"
        if view.rows_expected
        else None
    )
    if key and (kept := cache.get(key)) is not None:
        return kept
    found = _read_window(view)
    if key and found[2] >= view.rows_expected:
        cache.set(key, found, 3600)
    return found


def _read_window(
    view: RunView,
) -> tuple[datetime | None, datetime | None, int, int, list]:
    row = _rows(
        "SELECT min(time), max(time), count(*), count(DISTINCT end_user_id) "
        "FROM tracer_instant_eval_row WHERE task_id = %(task)s",
        {"task": view.task_id},
    )[0]
    tmin, tmax, total_rows, users = row
    if view.by_user:
        # Every user is one row of a user question: none is busier.
        return tmin, tmax, int(total_rows or 0), int(users or 0), []
    top = _rows(
        "SELECT end_user_id::text, max(user_label), count(*) AS n "
        "FROM tracer_instant_eval_row WHERE task_id = %(task)s AND end_user_id IS NOT NULL "
        "GROUP BY end_user_id ORDER BY n DESC, end_user_id LIMIT %(top)s",
        {"task": view.task_id, "top": TOP_USERS},
    )
    return tmin, tmax, int(total_rows or 0), int(users or 0), top


def _span(view: RunView, tmin, tmax) -> tuple[datetime, datetime]:
    now = timezone.now()
    start = view.date_from or tmin or now
    end = view.date_to or tmax or now
    if view.date_to is not None:
        # A view's window runs to the end of today; stop at the latest row.
        end = min(view.date_to, max(now, tmax or now))
    if tmin is not None:
        start = min(start, tmin)
    if tmax is not None:
        end = max(end, tmax + timedelta(milliseconds=1))
    if end <= start:
        end = start + timedelta(minutes=1)
    return start, end


# ─── Users ───────────────────────────────────────────────────────────────────
# A user's answer to a question is the most common answer among their
# sessions Jev answered; a tie goes to the tied answer whose latest session
# started last, and is low-confidence: its confidence is scaled by its share
# of the sessions (a 2–2 tie at 100% reads 50%), so a coin flip never reads
# as a sure answer. Sessions Jev couldn't answer don't vote, and a user with
# none answered couldn't be answered. A user waits for their answer until
# every one of their sessions has its own (a cancelled run rolls up what it
# read).


def _user_rollup(view: RunView, *, sessions: str = "") -> str:
    """The CTEs, ending in ``ur``, that make one row of each user of a user
    question, named like a row's columns: ``time`` their latest session's
    start (where the timeline puts them), ``model`` its model, ``turns`` and
    ``duration_ms`` summed over their sessions, ``error`` when any of them
    errored, ``sessions`` read, and per question their answer (l0, l1, …),
    the sessions' mean confidence in it (c0, …) and whether that is unsure
    (u0, …). ``sessions`` narrows the sessions read (``AND …``)."""
    k = len(view.config_ids)
    joins = "\n  ".join(
        f"LEFT JOIN tracer_instant_eval_answer a{i} ON a{i}.task_id = %(task)s "
        f"AND a{i}.config_id = %(cfg{i})s::uuid AND a{i}.row_id = r.id"
        for i in range(k)
    )
    answers = "".join(
        f", COALESCE(a{i}.label, '{PENDING}') AS l{i}, a{i}.confidence AS c{i}"
        for i in range(k)
    )
    votes = "".join(
        f""",
ur_v{i} AS (
  SELECT DISTINCT ON (end_user_id) end_user_id, label, tied > 1 AS tie,
         CASE WHEN tied > 1 THEN conf * n / answered ELSE conf END AS conf
  FROM (
    SELECT g.*, count(*) OVER (PARTITION BY end_user_id, n) AS tied,
           sum(n) OVER (PARTITION BY end_user_id)::float8 AS answered
    FROM (
      SELECT end_user_id, l{i} AS label, count(*) AS n, max(time) AS t, avg(c{i}) AS conf
      FROM ur_s WHERE l{i} NOT IN ('{PENDING}', '{ERROR}', '{SKIPPED}')
      GROUP BY end_user_id, l{i}
    ) g
  ) x
  ORDER BY end_user_id, n DESC, t DESC NULLS LAST, label
)"""
        for i in range(k)
    )
    waits = "".join(f", bool_or(l{i} = '{PENDING}') AS p{i}" for i in range(k))
    rolled = []
    for i in range(k):
        waiting = "false" if view.cancelled else f"u.p{i}"
        rolled.append(
            f"CASE WHEN {waiting} THEN '{PENDING}' WHEN v{i}.label IS NOT NULL "
            f"THEN v{i}.label WHEN u.p{i} THEN '{PENDING}' ELSE '{ERROR}' END AS l{i}, "
            f"CASE WHEN NOT {waiting} THEN v{i}.conf END AS c{i}, "
            f"NOT {waiting} AND (COALESCE(v{i}.conf < {UNSURE_BELOW}, false) "
            f"OR COALESCE(v{i}.tie, false)) AS u{i}"
        )
    columns = "".join(f",\n         {r}" for r in rolled)
    vote_joins = "".join(
        f" LEFT JOIN ur_v{i} v{i} ON v{i}.end_user_id = u.end_user_id" for i in range(k)
    )
    return f"""ur_s AS (
  SELECT r.id, r.end_user_id, r.time, r.model, r.turns, r.duration_ms, r.error,
         r.user_label{answers}
  FROM tracer_instant_eval_row r
  {joins}
  WHERE r.task_id = %(task)s AND r.end_user_id IS NOT NULL{sessions}
){votes},
ur_u AS (
  SELECT end_user_id, max(time) AS time, max(user_label) AS user_label,
         (array_agg(model ORDER BY time DESC NULLS LAST, id DESC))[1] AS model,
         sum(turns) AS turns, sum(duration_ms) AS duration_ms,
         bool_or(error) AS error, count(*) AS sessions{waits}
  FROM ur_s GROUP BY end_user_id
),
ur AS (
  SELECT u.end_user_id, u.time, u.user_label, u.model, u.turns, u.duration_ms,
         u.error, u.sessions{columns}
  FROM ur_u u{vote_joins}
)"""


def _user_params(view: RunView, params: dict) -> dict:
    params.update({f"cfg{i}": cfg for i, cfg in enumerate(view.config_ids)})
    return params


def user_answer_counts(view: RunView) -> tuple[int, int, dict]:
    """A user question's users, those with every answer in (or none to
    have), and each question's rolled-up counts: what History shows."""
    k = len(view.config_ids)
    labels = ", ".join(f"r.l{i}" for i in range(k))
    users = settled = 0
    counts: dict[str, dict[str, int]] = {cfg: {} for cfg in view.config_ids}
    for rec in _rows(
        f"WITH {_user_rollup(view)} SELECT {labels}, count(*) FROM ur r "
        f"GROUP BY {labels}",
        _user_params(view, {"task": view.task_id}),
    ):
        answers, n = rec[:k], int(rec[k])
        users += n
        if PENDING not in answers:
            settled += n
        for cfg, label in zip(view.config_ids, answers, strict=True):
            if label != PENDING:
                counts[cfg][label] = counts[cfg].get(label, 0) + n
    return users, settled, counts


_DIMS = ("b", "ukey", "model", "tk", "error")
_DIM_NAMES = ("time", "user", "model", "turns", "error")


def _statement(view: RunView, bucket: str) -> str:
    """One pass over the run's rows, each row carrying its answer to every
    question (l0, l1, …) from the narrow answer table, grouped by every
    dimension the charts need.

    Every row has exactly one combination of answers, so the counts of an
    hour, a model, a model's day, the whole run and the questions against
    each other all add up from (hour, model, answers); users, turns and
    errors need sets of their own. A user question's rows are its users,
    rolled up from their sessions (``_user_rollup``)."""
    k = len(view.config_ids)
    labels = [f"l{i}" for i in range(k)]
    pending_any = " OR ".join(f"{label} = '{PENDING}'" for label in labels)
    failed_any = " OR ".join(f"{label} IN ('{ERROR}', '{SKIPPED}')" for label in labels)
    joined = ", ".join(labels)
    unsure = ", ".join(_count(f"u{i}") for i in range(k))
    grouping = ", ".join([*_DIMS, *labels])
    turns = TURN_BUCKET.replace("turns", "r.turns")
    sets = ", ".join(f"({d}, {joined})" for d in ("b, model", "ukey", "tk", "error"))
    if view.by_user:
        # Every row is a user, so the user set is one "No user" entry the
        # summary leaves out.
        source = f"""{_user_rollup(view)},
w AS (
  SELECT {bucket} AS b, '__none__' AS ukey, r.model, {turns} AS tk, r.error,
         r.end_user_id, {", ".join(f"r.l{i}, r.u{i}" for i in range(k))}
  FROM ur r
)"""
    else:
        joins = "\n  ".join(
            f"LEFT JOIN tracer_instant_eval_answer a{i} ON a{i}.task_id = %(task)s "
            f"AND a{i}.config_id = %(cfg{i})s::uuid AND a{i}.row_id = r.id"
            for i in range(k)
        )
        row_labels = ",\n         ".join(
            f"COALESCE(a{i}.label, '{PENDING}') AS l{i}, "
            f"COALESCE(a{i}.confidence < {UNSURE_BELOW} "
            f"AND a{i}.label NOT IN ('{ERROR}', '{SKIPPED}'), false) AS u{i}"
            for i in range(k)
        )
        source = f"""w AS (
  SELECT {bucket} AS b,
         CASE WHEN r.end_user_id IS NULL THEN '__none__'
              WHEN r.end_user_id::text = ANY(%(top_users)s) THEN r.end_user_id::text
              ELSE '__other__' END AS ukey,
         r.model, {turns} AS tk, r.error, r.end_user_id,
         {row_labels}
  FROM tracer_instant_eval_row r
  {joins}
  WHERE r.task_id = %(task)s
)"""
    return f"""
WITH {source},
ws AS (
  SELECT w.*, CASE WHEN {pending_any} THEN 'p' WHEN {failed_any} THEN 'f' ELSE 'a' END AS st
  FROM w
)
SELECT GROUPING({grouping}), {grouping},
       {_count()}, {_count("st = 'p'")}, {_count("st = 'f'")},
       {_count("error")}, {unsure}
FROM ws GROUP BY GROUPING SETS ({sets})
"""


# How fast answers land: answers in the last two minutes and in all, and when
# the first and the last of them landed.
_PACE = f"""
SELECT count(*) FILTER (WHERE answered_at > now() - interval '{RATE_WINDOW_S} seconds'),
       count(*),
       (extract(epoch FROM max(answered_at)) * 1000)::bigint,
       (extract(epoch FROM min(answered_at)) * 1000)::bigint,
       count(*) FILTER (WHERE label NOT IN ('{ERROR}', '{SKIPPED}')),
       -- Minutes in which answers landed: how long the run actually ran,
       -- however long it sat paused or stopped in between.
       count(DISTINCT date_trunc('minute', answered_at))
FROM tracer_instant_eval_answer
WHERE task_id = %(task)s AND config_id = ANY(%(cfg_uuids)s::uuid[])
"""


# Users per answer: the 50 who get that answer most beyond the run's own
# share of it, by z-score, among users with 3 rows or more.
USERS_BY_ANSWER = 50
USERS_BY_ANSWER_MIN_ROWS = 3
# A finished run's users by answer (about 50 KB at 100,000 rows) are kept as
# long as the summaries they are part of (``FINISHED_SUMMARY_TTL`` in
# ``tracer.services.instant_eval_run``): only a summary being counted again
# reads them.
USERS_BY_ANSWER_TTL = 24 * 60 * 60

_LEANING = f"""
WITH ru AS (
  SELECT id, end_user_id FROM tracer_instant_eval_row
  WHERE task_id = %(task)s AND end_user_id IS NOT NULL
),
un AS (
  SELECT end_user_id, count(*) AS n FROM ru
  GROUP BY end_user_id HAVING count(*) >= %(min_rows)s
),
ak AS (
  SELECT a.config_id, ru.end_user_id, a.label, count(*) AS k
  FROM tracer_instant_eval_answer a JOIN ru ON ru.id = a.row_id
  WHERE a.task_id = %(task)s AND a.config_id = ANY(%(cfg_uuids)s::uuid[])
  GROUP BY 1, 2, 3
),
p0 AS (
  -- The run's share of each answer among its answered rows.
  SELECT config_id, label,
         count(*)::float8 / sum(count(*)) OVER (PARTITION BY config_id) AS p0
  FROM tracer_instant_eval_answer
  WHERE task_id = %(task)s AND config_id = ANY(%(cfg_uuids)s::uuid[])
    AND label NOT IN ('{ERROR}', '{SKIPPED}')
  GROUP BY config_id, label
),
scored AS (
  SELECT ak.config_id, ak.label, ak.end_user_id, ak.k, un.n,
         sum(ak.k) FILTER (WHERE ak.label NOT IN ('{ERROR}', '{SKIPPED}'))
           OVER (PARTITION BY ak.config_id, ak.end_user_id) AS answered
  FROM ak JOIN un USING (end_user_id)
),
ranked AS (
  SELECT s.config_id, s.label, s.end_user_id, row_number() OVER (
           PARTITION BY s.config_id, s.label
           ORDER BY (s.k - s.answered * p0.p0)
                    / NULLIF(sqrt(s.answered * p0.p0 * (1 - p0.p0)), 0)
                    DESC NULLS LAST,
                    s.n DESC, s.end_user_id::text
         ) AS rn
  FROM scored s JOIN p0 USING (config_id, label)
  WHERE s.label NOT IN ('{ERROR}', '{SKIPPED}') AND s.answered > 0
)
SELECT config_id::text, label, end_user_id::text, rn FROM ranked
WHERE rn <= %(top)s ORDER BY config_id, label, rn
"""


def kept_users_by_answer(view: RunView) -> dict:
    """``users_by_answer``, which doesn't depend on the time zone: counted
    once per answers version for a finished run (kept a day), every half
    minute for a live one."""
    key = (
        f"ie-users-by-answer:v2:{view.task_id}:{view.rows_version}:"
        f"{','.join(view.config_ids)}"
    )
    kept = cache.get(key)
    if kept is None:
        kept = users_by_answer(view)
        cache.set(key, kept, 30 if view.live else USERS_BY_ANSWER_TTL)
    return kept


def users_by_answer(view: RunView) -> dict:
    """{config: {label: [user entries]}}: per answer, the users who get it
    most beyond the run's share of it, ranked by z = (k - a*p0) /
    sqrt(a*p0*(1-p0)) over their answered rows a (ties to more rows, then
    the key), each counted like the entries of ``groups.user``. A z-score,
    unlike a share, puts a user at 60 of 67 above one at 15 of 15."""
    if not view.config_ids:
        return {}
    picked = _rows(
        _LEANING,
        {
            "task": view.task_id,
            "cfg_uuids": view.config_ids,
            "min_rows": USERS_BY_ANSWER_MIN_ROWS,
            "top": USERS_BY_ANSWER,
        },
    )
    shown = {cfg: set(view.labels.get(cfg) or []) for cfg in view.config_ids}
    picked = [
        (cfg, label, user)
        for cfg, label, user, _ in picked
        if not shown.get(cfg) or label in shown[cfg]
    ]
    users = sorted({user for _, _, user in picked})
    if not users:
        return {}
    k = len(view.config_ids)
    labels = ", ".join(f"l{i}" for i in range(k))
    joins = "\n".join(
        f"LEFT JOIN tracer_instant_eval_answer a{i} ON a{i}.task_id = %(task)s "
        f"AND a{i}.config_id = %(cfg{i})s::uuid AND a{i}.row_id = r.id"
        for i in range(k)
    )
    columns = ", ".join(f"COALESCE(a{i}.label, '{PENDING}') AS l{i}" for i in range(k))
    recs = _rows(
        f"SELECT r.end_user_id::text, max(r.user_label), {columns}, count(*), "
        "count(*) FILTER (WHERE r.error) "
        f"FROM tracer_instant_eval_row r {joins} "
        "WHERE r.task_id = %(task)s AND r.end_user_id = ANY(%(users)s::uuid[]) "
        f"GROUP BY r.end_user_id, {labels}",
        {
            "task": view.task_id,
            "users": users,
            **{f"cfg{i}": cfg for i, cfg in enumerate(view.config_ids)},
        },
    )
    entries: dict[str, dict] = {}
    for rec in recs:
        user, name, answers = rec[0], rec[1], rec[2 : 2 + k]
        n, errors = int(rec[2 + k]), int(rec[3 + k])
        entry = entries.setdefault(user, _entry(user, name or user))
        entry["n"] += n
        entry["errors"] += errors
        if PENDING in answers:
            entry["pending"] += n
        elif any(label in FAILED_LABELS for label in answers):
            entry["failed"] += n
        for i, label in enumerate(answers):
            if label != PENDING:
                _sparse_add(
                    entry["counts"],
                    view.config_ids[i],
                    ERROR if label in FAILED_LABELS else label,
                    n,
                )
    out: dict[str, dict[str, list]] = {}
    for cfg, label, user in picked:
        if user in entries:
            out.setdefault(cfg, {}).setdefault(label, []).append(entries[user])
    return out


# A user question's users by how consistently their sessions got an answer:
# per question and answer, users by sessions answered (n, 3 or more) and
# sessions with that answer (k), with the run's sessions per answer and its
# users per n, so the page can say how many users chance alone would put at
# k of n.
CONSISTENT_MIN_SESSIONS = 3

_CONSISTENCY = f"""
WITH s AS (
  SELECT a.config_id, r.end_user_id,
         CASE WHEN a.label IN ('{ERROR}', '{SKIPPED}') THEN '{ERROR}' ELSE a.label END AS label
  FROM tracer_instant_eval_row r JOIN tracer_instant_eval_answer a
    ON a.task_id = %(task)s AND a.row_id = r.id
   AND a.config_id = ANY(%(cfg_uuids)s::uuid[])
  WHERE r.task_id = %(task)s AND r.end_user_id IS NOT NULL
),
per AS (SELECT config_id, end_user_id, count(*) AS n FROM s GROUP BY 1, 2),
lab AS (
  SELECT config_id, end_user_id, label, count(*) AS k FROM s
  WHERE label <> '{ERROR}' GROUP BY 1, 2, 3
)
SELECT 'tier', lab.config_id::text, lab.label, per.n, lab.k, count(*)
FROM lab JOIN per USING (config_id, end_user_id) WHERE per.n >= %(min_n)s
GROUP BY 2, 3, 4, 5
UNION ALL
SELECT 'users', config_id::text, NULL, n, NULL, count(*) FROM per GROUP BY 2, 4
UNION ALL
SELECT 'sessions', config_id::text, label, NULL, NULL, count(*) FROM s GROUP BY 2, 3
"""


def user_consistency(view: RunView) -> dict:
    """{config: {sessions: {label: n}, users: {n: users}, tiers: {label:
    [[n, k, users]]}}} for a user question: ``sessions`` counts its answered
    sessions per answer (``__error__`` for the ones Jev couldn't answer),
    ``users`` its users by sessions answered, and ``tiers`` how many users
    had k of their n sessions get each answer, for n of 3 or more."""
    out = {cfg: {"sessions": {}, "users": {}, "tiers": {}} for cfg in view.config_ids}
    recs = _rows(
        _CONSISTENCY,
        {
            "task": view.task_id,
            "cfg_uuids": view.config_ids,
            "min_n": CONSISTENT_MIN_SESSIONS,
        },
    )
    for kind, cfg, label, n, k, count in recs:
        entry = out.get(cfg)
        if entry is None:
            continue
        if kind == "tier":
            entry["tiers"].setdefault(label, []).append([int(n), int(k), int(count)])
        elif kind == "users":
            entry["users"][str(int(n))] = int(count)
        else:
            entry["sessions"][label] = int(count)
    for entry in out.values():
        for tiers in entry["tiers"].values():
            tiers.sort(key=lambda t: (-t[0], -t[1]))
    return out


def _sparse_add(target: dict, cfg: str, label: str, n: int) -> None:
    if n:
        bucket = target.setdefault(cfg, {})
        bucket[label] = bucket.get(label, 0) + n


def _entry(key: str, label: str) -> dict:
    return {
        "key": key,
        "label": label,
        "n": 0,
        "errors": 0,
        "failed": 0,
        "pending": 0,
        "counts": {},
    }


def summary(view: RunView) -> dict:
    """The counts every chart needs, for one run."""
    tmin, tmax, total_rows, n_users, top = _window(view)
    start, end = _span(view, tmin, tmax)
    bin_ms = pick_bin((end - start).total_seconds() * 1000)
    bucket, grain = _bucket_sql(bin_ms)
    params = {
        "task": view.task_id,
        "task_s": view.task_id,
        "cfgs": view.config_ids,
        **{f"cfg{i}": cfg for i, cfg in enumerate(view.config_ids)},
        "tz": view.tz.key,
        "grain": grain,
        "top_users": [t[0] for t in top],
    }
    with transaction.atomic(), connection.cursor() as cur:
        # A guard, not the fix: the statistics of a run that just started lag
        # (ANALYZE after its bulk writes is the fix), and with a fresh task id
        # estimated at one row the planner joins row by row. This statement
        # reads every row of the run, so hash joins are always right for it.
        cur.execute("SET LOCAL enable_nestloop = off")
        cur.execute(_statement(view, bucket), params)
        result = cur.fetchall()
    pace = _rows(_PACE, {**params, "cfg_uuids": view.config_ids})[0]

    tz = view.tz
    local_start = start.astimezone(tz).replace(tzinfo=None)
    origin = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
    totals = {"n": 0, "pending": 0, "failed": 0}
    counts: dict[str, dict[str, int]] = {
        cfg: dict.fromkeys(view.labels.get(cfg, []), 0) | {ERROR: 0}
        for cfg in view.config_ids
    }
    unsure: dict[str, dict[str, int]] = {
        cfg: dict.fromkeys(view.labels.get(cfg, []), 0) for cfg in view.config_ids
    }
    cross: dict = {}
    # Per local time bucket: row stats and label counts.
    time_rows: dict[datetime, dict] = {}
    groups: dict[str, dict[str, dict]] = defaultdict(dict)
    user_names = {t[0]: t[1] or t[0] for t in top}

    k = len(view.config_ids)
    cfgs = view.config_ids
    nd = len(_DIMS)

    def group_key(dim: str, values) -> tuple[str, str] | None:
        _b, ukey, model, tk, err = values
        if dim == "user":
            if ukey == "__other__":
                return ukey, f"{max(0, n_users - len(top)):,} other users"
            if ukey == "__none__":
                return ukey, "No user"
            return ukey, user_names.get(ukey, ukey)
        if dim == "model":
            return (model or "__none__", model or "Unknown model")
        if dim == "turns":
            return (tk, TURN_LABELS[tk]) if tk else None
        if dim == "error":
            return ("error", ERROR_GROUP_LABEL) if err else ("ok", "No trace error")
        if dim == "model_day" and _b is not None:
            return (
                f"{model or '__none__'}|{_b:%Y-%m-%d}",
                f"{model or 'Unknown model'} · {_day_label(_b)}",
            )
        return None

    meta = (int(pace[0] or 0), int(pace[1] or 0), pace[2], pace[3])
    billed = int(pace[4] or 0)
    active_minutes = int(pace[5] or 0)
    # Per distinct combination of answers (a few dozen at most): the rows'
    # state and each question's answer as shown, worked out once.
    profiles: dict[tuple, tuple[str | None, tuple]] = {}

    def profile(answers: tuple) -> tuple[str | None, tuple]:
        found = profiles.get(answers)
        if found is None:
            if PENDING in answers:
                state = "pending"
            elif any(label in FAILED_LABELS for label in answers):
                state = "failed"
            else:
                state = None
            shown = tuple(
                (cfgs[i], ERROR if label in FAILED_LABELS else label)
                for i, label in enumerate(answers)
                if label != PENDING
            )
            found = profiles[answers] = (state, shown)
        return found

    def add_rows(target: dict, answers: tuple, n: int, errors: int) -> None:
        """n rows with these answers into one group entry or bin."""
        state, shown = profile(answers)
        target["n"] += n
        target["errors"] += errors
        if state:
            target[state] += n
        by_cfg = target["counts"]
        for cfg, label in shown:
            by_label = by_cfg.get(cfg)
            if by_label is None:
                by_label = by_cfg[cfg] = {}
            by_label[label] = by_label.get(label, 0) + n

    entries: dict[tuple, dict | None] = {}

    def entry_of(dim: str, values) -> dict | None:
        """A group's entry, looked up once per dimension value."""
        b, ukey, model, tk, err = values
        ident = (
            dim,
            (
                {"user": ukey, "model": model, "turns": tk, "error": err}.get(dim)
                if dim != "model_day"
                else (model, b.date() if b else None)
            ),
        )
        if ident in entries:
            return entries[ident]
        key = group_key(dim, values)
        found = entries[ident] = (
            groups[dim].setdefault(key[0], _entry(*key)) if key else None
        )
        return found

    def decode_rows() -> None:
        """Every count adds up from the grouping sets: (hour, model, answers)
        for the run, its hours, models and models' days, and one set per
        other dimension."""
        nonlocal totals
        combos: dict[tuple, int] = defaultdict(int)
        for rec in result:
            mask = rec[0]
            values = rec[1 : 1 + nd]
            answers = rec[1 + nd : 1 + nd + k]
            n = int(rec[1 + nd + k] or 0)
            errors = int(rec[4 + nd + k] or 0)
            missing = mask >> k
            present = [i for i in range(nd) if not missing & (1 << (nd - 1 - i))]
            if present == [0, 2]:
                # (hour, model, answers): the run, its hours, its models.
                combos[answers] += n
                b = values[0]
                bucket = time_rows.get(b)
                if bucket is None:
                    bucket = time_rows[b] = {
                        "n": 0,
                        "pending": 0,
                        "failed": 0,
                        "errors": 0,
                        "counts": {},
                    }
                add_rows(bucket, answers, n, errors)
                for dim in ("model", "model_day"):
                    if (entry := entry_of(dim, values)) is not None:
                        add_rows(entry, answers, n, errors)
                for i in range(k):
                    unsure_n = int(rec[5 + nd + k + i] or 0)
                    label = answers[i]
                    if unsure_n and label not in FAILED_LABELS and label != PENDING:
                        by_label = unsure.setdefault(cfgs[i], {})
                        by_label[label] = by_label.get(label, 0) + unsure_n
            elif len(present) == 1:
                dim = _DIM_NAMES[present[0]]
                if view.by_user and dim == "user":
                    continue
                if (entry := entry_of(dim, values)) is not None:
                    add_rows(entry, answers, n, errors)
        stats = {"n": 0, "pending": 0, "failed": 0, "errors": 0, "counts": {}}
        for answers, n in combos.items():
            add_rows(stats, answers, n, 0)
            for i, label in enumerate(answers):
                if label != PENDING:
                    by_label = counts.setdefault(cfgs[i], {})
                    by_label[label] = by_label.get(label, 0) + n
            # The questions' answers against each other, answered rows only.
            for a in range(k):
                for c in range(k):
                    la, lc = answers[a], answers[c]
                    if (
                        a == c
                        or la in (PENDING, ERROR, SKIPPED)
                        or lc
                        in (
                            PENDING,
                            ERROR,
                            SKIPPED,
                        )
                    ):
                        continue
                    by = (
                        cross.setdefault(cfgs[a], {})
                        .setdefault(cfgs[c], {})
                        .setdefault(la, {})
                    )
                    by[lc] = by.get(lc, 0) + n
        totals = {
            "n": stats["n"],
            "pending": stats["pending"],
            "failed": stats["failed"],
        }

    decode_rows()

    # Time: buckets into timeline bins, local hours, days, 3-hour blocks, weekdays.
    bins: dict[datetime, dict] = {}
    cursor = _bin_start(local_start, bin_ms, origin)
    local_end = end.astimezone(tz).replace(tzinfo=None)
    while cursor < local_end and len(bins) < MAX_BINS:
        bins[cursor] = {
            "t": _local_ms(cursor, tz),
            "n": 0,
            "pending": 0,
            "errors": 0,
            "failed": 0,
            "counts": {},
        }
        cursor = _step(cursor, bin_ms)

    time_entries: dict[datetime, list[dict]] = {}

    def entries_at(local: datetime) -> list[dict]:
        """The day-hour, day, 3-hour block and weekday a local hour falls in."""
        found = time_entries.get(local)
        if found is not None:
            return found
        hour = local.replace(minute=0, second=0, microsecond=0)
        h3 = hour.hour - hour.hour % 3
        keys = (
            (
                "day_hour",
                f"{local:%Y-%m-%d|%H}",
                f"{_day_label(local)}, {_hour_label(hour.hour)}",
            ),
            (
                "day",
                f"{local:%Y-%m-%d}",
                f"{WEEKDAYS[local.weekday()]} {_day_label(local)}",
            ),
            (
                "hour3",
                f"{local:%Y-%m-%d}|{h3:02d}",
                f"{_day_label(local)}, {_hour_range(h3, h3 + 3)}",
            ),
            ("weekday", str(local.isoweekday()), WEEKDAYS[local.weekday()]),
        )
        found = time_entries[local] = [
            groups[dim].setdefault(key, _entry(key, label)) for dim, key, label in keys
        ]
        return found

    def bin_at(local: datetime) -> dict | None:
        b = _bin_start(local, bin_ms, origin)
        if b not in bins:
            bins[b] = {
                "t": _local_ms(b, tz),
                "n": 0,
                "pending": 0,
                "errors": 0,
                "failed": 0,
                "counts": {},
            }
        return bins[b]

    def merge_counts(target: dict, by_cfg: dict) -> None:
        for cfg, by_label in by_cfg.items():
            into = target.setdefault(cfg, {})
            for label, n in by_label.items():
                into[label] = into.get(label, 0) + n

    for local, stats in time_rows.items():
        targets = [bin_at(local), *entries_at(local)]
        for target in targets:
            for key in ("n", "errors", "failed", "pending"):
                target[key] += stats[key]
            if stats.get("counts"):
                merge_counts(target["counts"], stats["counts"])

    def ordered(dim: str) -> list[dict]:
        entries = list(groups.get(dim, {}).values())
        if dim in ("day_hour", "day", "hour3", "weekday", "model_day"):
            return sorted(entries, key=lambda e: e["key"])
        if dim == "turns":
            return sorted(entries, key=lambda e: TURN_KEYS.index(e["key"]))
        if dim == "user":
            tail = [e for e in entries if e["key"] in ("__other__", "__none__")]
            head = [e for e in entries if e["key"] not in ("__other__", "__none__")]
            return sorted(head, key=lambda e: (-e["n"], e["label"])) + sorted(
                tail, key=lambda e: e["key"]
            )
        return sorted(entries, key=lambda e: (-e["n"], e["key"]))

    recent, settled_entries, last_ms, first_ms = meta
    pending = totals["pending"]
    failed = totals["failed"]
    total = totals["n"]
    for cfg in counts:
        counts[cfg].setdefault(ERROR, 0)
    return {
        "total": total,
        "answered": max(0, total - pending - failed),
        "failed": failed,
        "pending": 0 if view.cancelled else pending,
        "not_read": pending if view.cancelled else 0,
        "rows_stored": total_rows,
        # A user question's sessions read, behind its users.
        "sessions_read": total_rows if view.by_user else None,
        "counts": counts,
        "unsure": unsure,
        "timeline": {
            "bin_ms": bin_ms,
            "tz": tz.key,
            "from": int(start.timestamp() * 1000),
            "to": int(end.timestamp() * 1000),
            "bins": sorted(bins.values(), key=lambda b: b["t"]),
        },
        "groups": {
            **{
                dim: ordered(dim)
                for dim in (
                    "day_hour",
                    "day",
                    "hour3",
                    "weekday",
                    "user",
                    "model",
                    "model_day",
                    "turns",
                    "error",
                )
            },
            # Each answer's users who most consistently get it (a cohort can
            # hide among users too small for the busiest fifty). A user
            # question's users are one row each: how many got an answer in
            # k of their n sessions says it instead.
            "user_by_answer": {} if view.by_user else kept_users_by_answer(view),
            **({"user_consistency": user_consistency(view)} if view.by_user else {}),
        },
        "cross": cross if len(view.config_ids) > 1 else {},
        "_progress": {
            "recent_entries": recent,
            "settled_entries": settled_entries,
            "last_ms": last_ms,
            "first_ms": first_ms,
            # Answers Jev gave (a failed one isn't billed).
            "answers_given": billed,
            "active_minutes": active_minutes,
        },
    }


# ─── Rows ────────────────────────────────────────────────────────────────────
@dataclass
class RowFilter:
    """What the charts select. Every part narrows the rows (AND), except
    ``groups``, of which a row may match any."""

    answers: list[tuple[str | None, str]] = field(default_factory=list)
    unsure_config: str | None = None
    unsure: bool = False
    time_from: datetime | None = None
    time_to: datetime | None = None
    groups: list[str] = field(default_factory=list)
    user: str | None = None
    model: str | None = None
    error: bool | None = None
    q: str | None = None


def parse_time(value) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return datetime.fromtimestamp(float(text) / 1000, tz=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise BadFilter(f"Unreadable time: {text}") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


class _Params:
    """Named query parameters, each name used once."""

    def __init__(self, params: dict):
        self.params = params
        self.n = 0

    def __call__(self, value) -> str:
        self.n += 1
        name = f"p{self.n}"
        self.params[name] = value
        return f"%({name})s"


def _answer_exists(
    view: RunView, p: _Params, *, cfg: str | None, cond: str, negate=False
) -> str:
    """A row-level condition on a row's recorded answers (one per question)."""
    cfg_sql = (
        f"a.config_id = {p(cfg)}::uuid"
        if cfg
        else "a.config_id = ANY(%(cfg_uuids)s::uuid[])"
    )
    return (
        f"{'NOT ' if negate else ''}EXISTS (SELECT 1 FROM tracer_instant_eval_answer a "
        f"WHERE a.task_id = %(task)s AND a.row_id = r.id AND {cfg_sql} AND {cond})"
    )


def _contains(text: str) -> str:
    """A LIKE pattern matching ``text`` anywhere, its own wildcards (and the
    escape character) taken literally: use it with ``ESCAPE '\\'``."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _rolled(view: RunView, cfg: str | None, cond) -> str:
    """A condition on a user's rolled-up answers: to one question, or to any.
    ``cond(i)`` tests question i's columns."""
    indexes = (
        [view.config_ids.index(cfg)]
        if cfg in view.config_ids
        else []
        if cfg
        else range(len(view.config_ids))
    )
    tests = [cond(i) for i in indexes]
    return "(" + " OR ".join(tests) + ")" if tests else "false"


def _user_conditions(view: RunView, f: RowFilter, p: _Params) -> list[str]:
    """What the answer, unsure and text filters ask of a user question's
    rows, its users (``_user_rollup``): their rolled-up answers."""
    conds = []
    for cfg, answer in f.answers:
        value = p(ERROR if answer == SKIPPED else answer)
        conds.append(_rolled(view, cfg, lambda i, v=value: f"r.l{i} = {v}"))
    if f.unsure:
        conds.append(_rolled(view, f.unsure_config, lambda i: f"r.u{i}"))
    if f.q:
        like = p(_contains(f.q))
        conds.append(
            f"(r.user_label ILIKE {like} ESCAPE '\\' "
            f"OR r.end_user_id::text = {p(f.q)} OR EXISTS ("
            "SELECT 1 FROM tracer_instant_eval_row s WHERE s.task_id = %(task)s "
            "AND s.end_user_id = r.end_user_id "
            f"AND (s.excerpt ILIKE {like} ESCAPE '\\' OR s.row_id = {p(f.q)})))"
        )
    return conds


def _conditions(view: RunView, f: RowFilter, params: dict) -> list[str]:
    """The filter as conditions on ``r``: a run's rows, or a user question's
    users (``_user_rollup``), whose columns are named alike."""
    p = _Params(params)
    params.setdefault("cfg_uuids", view.config_ids)
    conds: list[str] = []
    if view.by_user:
        conds += _user_conditions(view, f, p)
        f = RowFilter(
            time_from=f.time_from,
            time_to=f.time_to,
            groups=f.groups,
            user=f.user,
            model=f.model,
            error=f.error,
        )
    for cfg, answer in f.answers:
        if answer == PENDING:
            if cfg:
                # Not answered yet: nothing recorded for that question.
                conds.append(_answer_exists(view, p, cfg=cfg, cond="true", negate=True))
            else:
                conds.append(
                    "(SELECT count(*) FROM tracer_instant_eval_answer a "
                    "WHERE a.task_id = %(task)s AND a.row_id = r.id "
                    f"AND a.config_id = ANY(%(cfg_uuids)s::uuid[])) < {len(view.config_ids)}"
                )
        elif answer == ERROR:
            conds.append(
                _answer_exists(
                    view, p, cfg=cfg, cond=f"a.label IN ('{ERROR}', '{SKIPPED}')"
                )
            )
        else:
            conds.append(
                _answer_exists(view, p, cfg=cfg, cond=f"a.label = {p(answer)}")
            )
    if f.unsure:
        conds.append(
            _answer_exists(
                view,
                p,
                cfg=f.unsure_config,
                cond=f"a.label NOT IN ('{ERROR}', '{SKIPPED}') "
                f"AND a.confidence < {UNSURE_BELOW}",
            )
        )
    if f.time_from:
        conds.append(f"r.time >= {p(f.time_from)}")
    if f.time_to:
        conds.append(f"r.time < {p(f.time_to)}")
    if f.user:
        conds.append(
            f"r.end_user_id = {p(f.user)}::uuid"
            if _is_uuid(f.user)
            else f"r.user_label = {p(f.user)}"
        )
    if f.model is not None:
        conds.append(f"r.model = {p('' if f.model == '__none__' else f.model)}")
    if f.error is not None:
        conds.append("r.error" if f.error else "NOT r.error")
    if f.q:
        like = p(_contains(f.q))
        conds.append(
            f"(r.excerpt ILIKE {like} ESCAPE '\\' OR r.user_label ILIKE {like} "
            f"ESCAPE '\\' OR r.row_id = {p(f.q)})"
        )
    if f.groups:
        conds.append(
            "(" + " OR ".join(_group_condition(view, g, p) for g in f.groups) + ")"
        )
    return conds


def _local_range(start: datetime, hours: int, p: _Params) -> str:
    return (
        f"(r.time AT TIME ZONE %(tz)s) >= {p(start)} "
        f"AND (r.time AT TIME ZONE %(tz)s) < {p(start + timedelta(hours=hours))}"
    )


def _top_users(task_id: str) -> list[str]:
    return [
        t[0]
        for t in _rows(
            "SELECT end_user_id::text, count(*) AS n FROM tracer_instant_eval_row "
            "WHERE task_id = %(task)s AND end_user_id IS NOT NULL "
            "GROUP BY end_user_id ORDER BY n DESC, end_user_id LIMIT %(top)s",
            {"task": task_id, "top": TOP_USERS},
        )
    ]


def _group_condition(view: RunView, group: str, p: _Params) -> str:
    dim, _, key = group.partition(":")
    try:
        if dim in ("day_hour", "hour3"):
            day, _, hour = key.partition("|")
            start = datetime.strptime(f"{day} {int(hour):02d}", "%Y-%m-%d %H")
            return _local_range(start, 1 if dim == "day_hour" else 3, p)
        if dim == "day":
            return _local_range(datetime.strptime(key, "%Y-%m-%d"), 24, p)
        if dim == "weekday":
            return f"extract(isodow FROM r.time AT TIME ZONE %(tz)s) = {p(int(key))}"
    except ValueError:
        raise BadFilter(f"Unreadable group: {group}") from None
    if dim == "user":
        if key == "__none__":
            return "r.end_user_id IS NULL"
        if key == "__other__":
            return (
                "(r.end_user_id IS NOT NULL AND NOT "
                f"(r.end_user_id::text = ANY({p(_top_users(view.task_id))})))"
            )
        if _is_uuid(key):
            return f"r.end_user_id = {p(key)}::uuid"
        return f"r.user_label = {p(key)}"
    if dim == "model":
        return f"r.model = {p('' if key == '__none__' else key)}"
    if dim == "model_day":
        model, _, day = key.rpartition("|")
        try:
            start = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise BadFilter(f"Unreadable group: {group}") from None
        return (
            f"(r.model = {p('' if model == '__none__' else model)} "
            f"AND {_local_range(start, 24, p)})"
        )
    if dim == "turns":
        if key == "__none__":
            return "r.turns IS NULL"
        if key not in TURN_KEYS:
            raise BadFilter(f"Unreadable group: {group}")
        return f"({TURN_BUCKET.replace('turns', 'r.turns')}) = {p(key)}"
    if dim == "error" and key in ("error", "ok"):
        return "r.error" if key == "error" else "NOT r.error"
    if dim == "consistent" and view.by_user:
        return _consistent_condition(group, key, p)
    raise BadFilter(f"Unreadable group: {group}")


def _consistent_condition(group: str, key: str, p: _Params) -> str:
    """``consistent:<config>|<answer>|<k>|<n>``: the users k of whose n
    answered sessions got that answer to that question."""
    cfg, _, rest = key.partition("|")
    rest, _, n = rest.rpartition("|")
    label, _, k = rest.rpartition("|")
    if not (_is_uuid(cfg) and label and k.isdigit() and n.isdigit()):
        raise BadFilter(f"Unreadable group: {group}")
    return (
        "r.end_user_id IN (SELECT s.end_user_id FROM tracer_instant_eval_row s "
        "JOIN tracer_instant_eval_answer a ON a.task_id = %(task)s "
        f"AND a.row_id = s.id AND a.config_id = {p(cfg)}::uuid "
        "WHERE s.task_id = %(task)s AND s.end_user_id IS NOT NULL "
        f"GROUP BY s.end_user_id HAVING count(*) = {p(int(n))} "
        f"AND count(*) FILTER (WHERE a.label = {p(label)}) = {p(int(k))})"
    )


def _cursor_encode(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def _cursor_decode(token: str | None) -> dict:
    if not token:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (ValueError, TypeError):
        raise BadFilter("Unreadable cursor") from None


_ROW_COLUMNS = (
    "r.id, r.row_id, r.time, r.end_user_id, r.user_label, r.model, r.turns, "
    "r.duration_ms, r.error, r.excerpt, r.trace_id"
)


def _row_dict(rec) -> dict:
    (
        _,
        row_id,
        time,
        end_user_id,
        user_label,
        model,
        turns,
        duration_ms,
        error,
        excerpt,
        trace_id,
    ) = rec[:11]
    return {
        "id": row_id,
        "time": time.isoformat() if time else None,
        "user_id": user_label or "",
        "end_user_id": str(end_user_id) if end_user_id else None,
        "excerpt": excerpt or "",
        "label": excerpt or row_id,
        "trace_id": trace_id,
        "facets": {
            "model": model or None,
            "turns": turns,
            "duration_ms": duration_ms,
            "error": bool(error),
        },
        "answers": {},
    }


def recorded_answers(view: RunView, row_pks: list[int]) -> dict[int, dict]:
    """Each row's recorded answer and confidence per question, without the
    explanations: what the charts' squares need, from the narrow table."""
    if not row_pks:
        return {}
    recs = _rows(
        "SELECT row_id, config_id::text, label, confidence FROM tracer_instant_eval_answer "
        "WHERE task_id = %(task)s AND row_id = ANY(%(pks)s) "
        "AND config_id = ANY(%(cfgs)s::uuid[])",
        {"task": view.task_id, "pks": row_pks, "cfgs": view.config_ids},
    )
    out: dict[int, dict] = defaultdict(dict)
    for pk, cfg, label, confidence in recs:
        out[pk][cfg] = {
            "answer": label,
            "explanation": "",
            "confidence": confidence,
            "status": "errored" if label in FAILED_LABELS else "completed",
        }
    return out


def answers_for(
    view: RunView, row_ids: list[str], *, explain: bool = True
) -> dict[str, dict]:
    """Each row's answer, explanation and confidence per question."""
    if not row_ids:
        return {}
    if view.row_type == "spans":
        match, ids = "l.observation_span_id = ANY(%(ids)s)", list(row_ids)
    else:
        column = "l.trace_id" if view.row_type == "traces" else "l.trace_session_id"
        ids = [str(uuid.UUID(r)) for r in row_ids if _is_uuid(r)]
        match = f"{column} = ANY(%(ids)s::uuid[])"
    recs = _rows(
        f"SELECT {RID[view.row_type]}, l.custom_eval_config_id::text, {LABEL}, "
        f"{'left(COALESCE(l.eval_explanation, %(blank)s), 2000)' if explain else '%(blank)s'}, "
        f"{CONFIDENCE}, l.status "
        "FROM tracer_eval_logger l "
        "WHERE l.eval_task_id = %(task_s)s AND l.deleted = false "
        f"AND l.custom_eval_config_id::text = ANY(%(cfgs)s) AND {match}",
        {"task_s": view.task_id, "cfgs": view.config_ids, "ids": ids, "blank": ""},
    )
    out: dict[str, dict] = defaultdict(dict)
    for rid, cfg, label, explanation, confidence, status in recs:
        pending = label == PENDING
        out[rid][cfg] = {
            "answer": None if pending else label,
            "explanation": explanation or "",
            "confidence": confidence,
            "status": "pending" if (pending and view.cancelled) else str(status or ""),
        }
    return out


def _row_order(
    view: RunView, sort: str, sort_config: str | None, state: dict, params: dict
) -> tuple[str, str, str, str]:
    """(joins, sort key, ORDER BY, keyset) that read a run's rows in ``sort``
    order after the row ``state`` names: the rows page and the export read
    them alike."""
    if sort == "confidence":
        params["s_cfg"] = sort_config or (view.config_ids[0] if view.config_ids else "")
        joins = (
            "LEFT JOIN tracer_instant_eval_answer c ON c.task_id = %(task)s "
            "AND c.config_id = %(s_cfg)s::uuid AND c.row_id = r.id"
        )
        # Unanswered rows sort last, as a confidence of 2. The cursor carries
        # the last row's (confidence, id): a page deep in a run is read from
        # there, not by counting past an offset.
        keyset = ""
        if state.get("c") is not None:
            params["c_c"] = float(state["c"])
            params["c_id"] = int(state["i"])
            keyset = " AND (COALESCE(c.confidence, 2), r.id) > (%(c_c)s, %(c_id)s)"
        return (
            joins,
            ", COALESCE(c.confidence, 2)",
            "COALESCE(c.confidence, 2) ASC, r.id ASC",
            keyset,
        )
    # The (task, time, id) index serves both directions. A row normally
    # has a time (its first span's), but the column is nullable: NULLs
    # come first newest-first and last oldest-first (Postgres' defaults,
    # so the index still orders), and the cursor marks a page that ended
    # among them, so no row is skipped.
    desc = sort != "time"
    order = "r.time DESC, r.id DESC" if desc else "r.time ASC, r.id ASC"
    keyset = ""
    if state.get("n"):
        params["c_id"] = int(state["i"])
        keyset = (
            " AND ((r.time IS NULL AND r.id < %(c_id)s) OR r.time IS NOT NULL)"
            if desc
            else " AND r.time IS NULL AND r.id > %(c_id)s"
        )
    elif state.get("t") is not None:
        params["c_t"] = parse_time(state["t"])
        params["c_id"] = int(state["i"])
        keyset = (
            " AND (r.time, r.id) < (%(c_t)s, %(c_id)s)"
            if desc
            else " AND ((r.time, r.id) > (%(c_t)s, %(c_id)s) OR r.time IS NULL)"
        )
    return "", "", order, keyset


def _row_state(sort: str, rec) -> dict:
    """Where a page in ``sort`` order ended: its last row (read with
    ``_ROW_COLUMNS`` and, for confidence, the sort key after them)."""
    if sort == "confidence":
        return {"c": rec[11], "i": rec[0]}
    if rec[2] is None:
        return {"n": 1, "i": rec[0]}
    return {"t": rec[2].isoformat(), "i": rec[0]}


def rows_page(
    view: RunView,
    f: RowFilter,
    *,
    cursor: str | None,
    page_size: int,
    sort: str,
    sort_config: str | None = None,
    compact: bool = False,
) -> dict:
    params: dict = {
        "task": view.task_id,
        "task_s": view.task_id,
        "cfgs": view.config_ids,
        "tz": view.tz.key,
    }
    state = _cursor_decode(cursor)
    if view.by_user:
        return _user_rows_page(
            view, f, params, state, page_size, sort=sort, sort_config=sort_config
        )
    conds = _conditions(view, f, params)
    where = " AND ".join(["r.task_id = %(task)s", *conds])
    joins, sort_key, order, keyset = _row_order(view, sort, sort_config, state, params)
    params["lim"] = page_size + 1
    recs = _rows(
        f"SELECT {_ROW_COLUMNS}{sort_key} FROM tracer_instant_eval_row r {joins} "
        f"WHERE {where}{keyset} ORDER BY {order} LIMIT %(lim)s",
        params,
    )
    more = len(recs) > page_size
    recs = recs[:page_size]
    rows = [_row_dict(rec) for rec in recs]
    if compact:
        recorded = recorded_answers(view, [rec[0] for rec in recs])
        for rec, row in zip(recs, rows, strict=True):
            row["answers"] = recorded.get(rec[0], {})
    else:
        answers = answers_for(view, [r["id"] for r in rows])
        for row in rows:
            row["answers"] = answers.get(row["id"], {})
    next_cursor = None
    if more and recs:
        next_cursor = _cursor_encode(_row_state(sort, recs[-1]))
    total = _rows(
        f"SELECT count(*) FROM tracer_instant_eval_row r WHERE {where}", params
    )[0][0]
    return {"rows": rows, "next_cursor": next_cursor, "total": int(total)}


def _user_sessions_tally(view: RunView, users: list[str]) -> dict[str, dict]:
    """{user: {config: {label: sessions}}}: the answers behind each user's
    rolled-up answer, from the narrow table."""
    if not users:
        return {}
    out: dict[str, dict] = defaultdict(dict)
    for user, cfg, label, n in _rows(
        "SELECT r.end_user_id::text, a.config_id::text, a.label, count(*) "
        "FROM tracer_instant_eval_row r JOIN tracer_instant_eval_answer a "
        "ON a.task_id = %(task)s AND a.row_id = r.id "
        "AND a.config_id = ANY(%(cfgs)s::uuid[]) "
        "WHERE r.task_id = %(task)s AND r.end_user_id = ANY(%(users)s::uuid[]) "
        "GROUP BY 1, 2, 3",
        {"task": view.task_id, "cfgs": view.config_ids, "users": users},
    ):
        out[user].setdefault(cfg, {})[label] = int(n)
    return out


def _user_answers(view: RunView, rec, tally: dict) -> dict:
    """One user's answer to each question, from a rollup row (its l0, c0,
    l1, c1, … at the end) and the sessions behind it."""
    k = len(view.config_ids)
    rolled = rec[len(rec) - 2 * k :]
    out = {}
    for i, cfg in enumerate(view.config_ids):
        label, confidence = rolled[2 * i], rolled[2 * i + 1]
        waiting = label == PENDING
        out[cfg] = {
            "answer": None if waiting else label,
            "explanation": "",
            "confidence": confidence,
            # Sessions per answer: the rolled-up answer's own, of them all.
            "counts": tally.get(cfg, {}),
            "status": (
                ("pending" if view.cancelled else "running") if waiting else "completed"
            ),
        }
    return out


_USER_COLUMNS = (
    "r.end_user_id::text, r.user_label, r.time, r.sessions, r.model, r.turns, "
    "r.duration_ms, r.error"
)


_USER_KEY_CHUNK = 1000
_USER_KEY_TTL = 3600


def _user_page_keys(
    view: RunView,
    where: str,
    params: dict,
    sort: str,
    state: dict,
    page_size: int,
    *,
    sort_config: str | None = None,
) -> tuple[list[tuple], int]:
    """Seek through a versioned, filtered user selector and its exact total.

    Only sort keys and user ids are kept, in small blocks: a deep page reads
    its blocks, not every user's sessions or a 100k-user cache value. Answers
    and facets are rolled up separately for just the selected users.
    """
    if sort == "confidence":
        i = view.config_ids.index(sort_config) if sort_config in view.config_ids else 0
        nulls, value = "0", f"COALESCE(r.c{i}, 2)"
    else:
        # Preserve user paging's NULLS FIRST ascending / NULLS LAST descending,
        # with user ids ascending in both directions. Integer microseconds
        # avoid losing precision when a time key crosses a JSON cursor.
        nulls = (
            "(r.time IS NOT NULL)::int" if sort == "time" else "(r.time IS NULL)::int"
        )
        sign = "" if sort == "time" else "-"
        value = f"COALESCE({sign}(extract(epoch FROM r.time) * 1000000)::bigint, 0)"
    after = state.get("k")
    if after is not None:
        if (
            not isinstance(after, list)
            or len(after) != 3
            or after[0] not in (0, 1)
            or not isinstance(after[1], int | float)
            or not isinstance(after[2], str)
            or not _is_uuid(after[2])
        ):
            raise BadFilter("Unreadable cursor")
        after = tuple(after)
    digest = hashlib.sha256(
        json.dumps(
            [view.rows_version, view.cancelled, view.live, where, params, nulls, value],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    key = f"ie-user-keys:v1:{digest}"
    kept = cache.get(key)
    if kept is not None:
        token, starts, total = kept
        block = max(0, bisect_right(starts, after) - 1) if after else 0
        selected = []
        while block < len(starts) and len(selected) <= page_size:
            keys = cache.get(f"{key}:chunk:{token}:{block}")
            if keys is None:
                # Evicted/expired blocks are rebuilt as one selector; never
                # return a partial page with the old total.
                break
            start = bisect_right(keys, after) if after else 0
            selected.extend(keys[start : start + page_size + 1 - len(selected)])
            block += 1
        else:
            return selected, total
    keys = _rows(
        f"WITH {_user_rollup(view)} SELECT {nulls}, {value}, r.end_user_id::text "
        f"FROM ur r WHERE {where} ORDER BY 1, 2, 3",
        params,
    )
    token = uuid.uuid4().hex
    chunks = {
        f"{key}:chunk:{token}:{start // _USER_KEY_CHUNK}": keys[
            start : start + _USER_KEY_CHUNK
        ]
        for start in range(0, len(keys), _USER_KEY_CHUNK)
    }
    ttl = 30 if view.live else _USER_KEY_TTL
    cache.set_many(chunks, ttl + 60)
    cache.set(key, (token, keys[::_USER_KEY_CHUNK], len(keys)), ttl)
    start = bisect_right(keys, after) if after else 0
    return keys[start : start + page_size + 1], len(keys)


def _user_rows_page(
    view: RunView,
    f: RowFilter,
    params: dict,
    state: dict,
    page_size: int,
    *,
    sort: str,
    sort_config: str | None,
) -> dict:
    """A user question lists users, each with their rolled-up answers and
    the sessions behind them, filtered by those answers."""
    _user_params(view, params)
    where = " AND ".join(_conditions(view, f, params)) or "true"
    keys, total = _user_page_keys(
        view, where, params, sort, state, page_size, sort_config=sort_config
    )
    more = len(keys) > page_size
    keys = keys[:page_size]
    users = [key[2] for key in keys]
    params["users"] = users
    rolled = ", ".join(f"r.l{i}, r.c{i}" for i in range(len(view.config_ids)))
    rollup = _user_rollup(view, sessions=" AND r.end_user_id = ANY(%(users)s::uuid[])")
    by_user = {
        rec[0]: rec
        for rec in (
            _rows(f"WITH {rollup} SELECT {_USER_COLUMNS}, {rolled} FROM ur r", params)
            if users
            else []
        )
    }
    recs = [by_user[user] for user in users if user in by_user]
    tally = _user_sessions_tally(view, users)
    latest = dict(
        _rows(
            "SELECT DISTINCT ON (end_user_id) end_user_id::text, excerpt "
            "FROM tracer_instant_eval_row "
            "WHERE task_id = %(task)s AND end_user_id = ANY(%(users)s::uuid[]) "
            "ORDER BY end_user_id, time DESC NULLS LAST, id DESC",
            {"task": view.task_id, "users": users},
        )
        if users
        else []
    )
    rows = []
    for rec in recs:
        uid, label, time, sessions, model, turns, duration, error = rec[:8]
        rows.append(
            {
                "id": uid,
                "time": time.isoformat() if time else None,
                "user_id": label or "",
                "end_user_id": uid,
                "excerpt": latest.get(uid) or "",
                "label": label or uid,
                "trace_id": None,
                "sessions": int(sessions),
                "facets": {
                    "model": model or None,
                    "turns": int(turns) if turns is not None else None,
                    "duration_ms": int(duration) if duration is not None else None,
                    "error": bool(error),
                },
                "answers": _user_answers(view, rec, tally.get(uid, {})),
            }
        )
    return {
        "rows": rows,
        "next_cursor": _cursor_encode({"k": keys[-1]}) if more and keys else None,
        "total": int(total),
    }


# ─── Export ──────────────────────────────────────────────────────────────────
EXPORT_PAGE = 5000


# A cell a spreadsheet would read as a formula. The excerpt is the traced
# app's user message, so it is attacker-controlled.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    if isinstance(value, str) and value.startswith(_FORMULA_LEAD):
        return "'" + value
    return value


def _csv_line(values) -> str:
    import csv
    import io

    buf = io.StringIO()
    csv.writer(buf).writerow([_csv_cell(v) for v in values])
    return buf.getvalue()


EXPORT_INCOMPLETE = "# export incomplete: a read failed; export again\n"


def marked_export(lines):
    """The export's lines, ending in an EXPORT_INCOMPLETE line when a read
    fails after the headers were sent, so a truncated file never passes for
    a whole one."""
    try:
        yield from lines
    except Exception:
        logger.exception("instant_eval_export_failed")
        yield EXPORT_INCOMPLETE


def _selected_users(view: RunView, f: RowFilter) -> list[str] | None:
    """The users of a user question the filter selects, or None for all."""
    params = _user_params(view, {"task": view.task_id, "tz": view.tz.key})
    conds = _conditions(view, f, params)
    if not conds:
        return None
    return [
        uid
        for (uid,) in _rows(
            f"WITH {_user_rollup(view)} SELECT r.end_user_id::text FROM ur r "
            f"WHERE {' AND '.join(conds)}",
            params,
        )
    ]


def iter_export(
    view: RunView,
    f: RowFilter,
    questions: list[dict],
    *,
    sessions: bool = False,
    sort: str = "-time",
    sort_config: str | None = None,
):
    """CSV lines for every row the filter selects, read page by page in
    ``sort`` order, as the rows table lists them. A user question writes a
    line per user (``_iter_user_export``), or with ``sessions`` a line per
    session of the users the filter selects."""
    if view.by_user and not sessions:
        yield from _iter_user_export(
            view, f, questions, sort=sort, sort_config=sort_config
        )
        return
    names = {q["config_id"]: q["question"] for q in questions}
    header = [
        "id",
        "time",
        "user_id",
        "end_user_id",
        "excerpt",
        "model",
        "turns",
        "duration_ms",
        "error",
    ]
    for cfg in view.config_ids:
        q = names.get(cfg, cfg)
        header += [f"{q} · answer", f"{q} · confidence", f"{q} · explanation"]

    yield _csv_line(header)
    params: dict = {
        "task": view.task_id,
        "task_s": view.task_id,
        "cfgs": view.config_ids,
        "tz": view.tz.key,
    }
    if view.by_user:
        where = "r.task_id = %(task)s"
        users = _selected_users(view, f)
        if users is not None:
            params["users"] = users
            where += " AND r.end_user_id = ANY(%(users)s::uuid[])"
    else:
        conds = _conditions(view, f, params)
        where = " AND ".join(["r.task_id = %(task)s", *conds])
    state: dict = {}
    while True:
        joins, sort_key, order, keyset = _row_order(
            view, sort, sort_config, state, params
        )
        params["lim"] = EXPORT_PAGE
        recs = _rows(
            f"SELECT {_ROW_COLUMNS}{sort_key} FROM tracer_instant_eval_row r "
            f"{joins} WHERE {where}{keyset} ORDER BY {order} LIMIT %(lim)s",
            params,
        )
        if not recs:
            return
        state = _row_state(sort, recs[-1])
        answers = answers_for(view, [rec[1] for rec in recs])
        for rec in recs:
            row = _row_dict(rec)
            facets = row["facets"]
            values = [
                row["id"],
                row["time"] or "",
                row["user_id"],
                row["end_user_id"] or "",
                row["excerpt"],
                facets["model"] or "",
                "" if facets["turns"] is None else facets["turns"],
                "" if facets["duration_ms"] is None else facets["duration_ms"],
                "true" if facets["error"] else "false",
            ]
            for cfg in view.config_ids:
                ans = answers.get(row["id"], {}).get(cfg) or {}
                answer = ans.get("answer")
                values += [
                    "" if answer is None else answer,
                    (
                        ""
                        if ans.get("confidence") is None
                        else round(ans["confidence"], 3)
                    ),
                    ans.get("explanation", ""),
                ]
            yield _csv_line(values)
        if len(recs) < EXPORT_PAGE:
            return


def _iter_user_export(
    view: RunView,
    f: RowFilter,
    questions: list[dict],
    *,
    sort: str = "-time",
    sort_config: str | None = None,
):
    """A line per user the filter selects: their rolled-up answer to each
    question, its sessions' mean confidence, and the sessions behind it, by
    answer. Read a page of users at a time, in ``sort`` order as the rows
    table lists them."""
    names = {q["config_id"]: q["question"] for q in questions}
    found: dict[str, set] = defaultdict(set)
    for cfg, label in _rows(
        "SELECT DISTINCT config_id::text, label FROM tracer_instant_eval_answer "
        "WHERE task_id = %(task)s AND config_id = ANY(%(cfgs)s::uuid[])",
        {"task": view.task_id, "cfgs": view.config_ids},
    ):
        if label not in FAILED_LABELS:
            found[cfg].add(label)
    labels = {}
    for cfg in view.config_ids:
        listed = list(view.labels.get(cfg) or [])
        labels[cfg] = listed + sorted(found[cfg] - set(listed))
    header = [
        "user_id",
        "end_user_id",
        "latest_session",
        "sessions_read",
        "model",
        "turns",
        "duration_ms",
        "error",
    ]
    for cfg in view.config_ids:
        q = names.get(cfg, cfg)
        header += [f"{q} · answer", f"{q} · confidence"]
        header += [f"{q} · {label} sessions" for label in labels[cfg]]
        header.append(f"{q} · couldn't answer sessions")
    yield _csv_line(header)

    state: dict = {}
    while True:
        params = {
            "task": view.task_id,
            "task_s": view.task_id,
            "cfgs": view.config_ids,
            "tz": view.tz.key,
        }
        page = _user_rows_page(
            view, f, params, state, EXPORT_PAGE, sort=sort, sort_config=sort_config
        )
        for row in page["rows"]:
            facets = row["facets"]
            values = [
                row["user_id"],
                row["end_user_id"],
                row["time"] or "",
                row["sessions"],
                facets["model"] or "",
                "" if facets["turns"] is None else facets["turns"],
                "" if facets["duration_ms"] is None else facets["duration_ms"],
                "true" if facets["error"] else "false",
            ]
            for cfg in view.config_ids:
                ans = row["answers"][cfg]
                by_label = ans["counts"]
                values += [
                    ans["answer"] or "",
                    "" if ans["confidence"] is None else round(ans["confidence"], 3),
                    *(by_label.get(label, 0) for label in labels[cfg]),
                    sum(by_label.get(label, 0) for label in FAILED_LABELS),
                ]
            yield _csv_line(values)
        if not page["next_cursor"]:
            return
        state = _cursor_decode(page["next_cursor"])
