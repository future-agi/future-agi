"""Independent generator for api_contracts/schedules/next-runs.golden.json.

Usage (from the repo root): python3 scripts/generate-next-runs-golden.py \
    > api_contracts/schedules/next-runs.golden.json

Written separately from the JS cronModel (Python zoneinfo, its own cron
matching), so the golden file checks the JS rather than echoing it. Rules
(notify DESIGN 2.2): a wall time that doesn't exist runs at the next valid
minute; one that happens twice runs once, at the first; hourly / every N
hours are UTC intervals from the anchor; day and weekday combine as OR when
both are restricted.
"""
import json, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc

def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

def expand(field, lo, hi):
    out = set()
    for part in field.split(","):
        rng, _, step = part.partition("/")
        step = int(step) if step else 1
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = map(int, rng.split("-"))
        else:
            a = int(rng); b = hi if _ else a
        out.update(range(a, b + 1, step))
    return out

def local_to_utc(y, m, d, h, mi, zi):
    """(instant, shifted) for a local wall time, gap -> end of gap, overlap -> first."""
    naive = datetime(y, m, d, h, mi)
    cands = []
    for fold in (0, 1):
        t = naive.replace(tzinfo=zi, fold=fold).astimezone(UTC)
        if t.astimezone(zi).replace(tzinfo=None) == naive:
            cands.append(t)
    if cands:
        return min(cands), False
    # gap: scan forward minute by minute from 3 h before for the first wall > naive
    t = naive.replace(tzinfo=zi, fold=0).astimezone(UTC) - timedelta(hours=3)
    t = t.replace(second=0, microsecond=0)
    while t.astimezone(zi).replace(tzinfo=None) <= naive:
        t += timedelta(minutes=1)
    return t, True

def cron_runs(cron, tz, frm, n):
    mi_f, h_f, dom_f, mon_f, dow_f = cron.split()
    mins = expand(mi_f, 0, 59); hours = expand(h_f, 0, 23)
    doms = expand(dom_f, 1, 31); mons = expand(mon_f, 1, 12)
    dows = {d % 7 for d in expand(dow_f, 0, 7)}
    dom_r = len(doms) < 31; dow_r = len(dows) < 7
    zi = ZoneInfo(tz)
    start = frm.astimezone(zi).date() - timedelta(days=1)
    out = []
    for i in range(4000):
        day = start + timedelta(days=i)
        cron_dow = (day.weekday() + 1) % 7  # Python Mon=0 -> cron Mon=1, Sun=0
        if day.month not in mons:
            continue
        dm = day.day in doms; wm = cron_dow in dows
        if not ((dm or wm) if (dom_r and dow_r) else (dm and wm)):
            continue
        for h in sorted(hours):
            for mi in sorted(mins):
                t, shifted = local_to_utc(day.year, day.month, day.day, h, mi, zi)
                if t > frm and all(t != o["t"] for o in out):
                    out.append({"t": t, "shifted_from": f"{h:02d}:{mi:02d}" if shifted else None})
        out.sort(key=lambda o: o["t"])
        if len(out) >= n:
            break
    return [{"at": iso(o["t"]), "shifted_from": o["shifted_from"]} for o in out[:n]]

def interval_runs(c, frm, n):
    step = timedelta(minutes=60 if c["preset"] == "hourly" else c["interval_min"])
    if c.get("anchor_at"):
        a = parse_iso(c["anchor_at"])
        k = 0 if a > frm else (frm - a) // step + 1
        first = a + k * step
    else:
        zi = ZoneInfo(c["tz"])
        t = frm.replace(second=0, microsecond=0) + timedelta(minutes=1)
        while t.astimezone(zi).minute != c["minute"]:
            t += timedelta(minutes=1)
        first = t
    return [{"at": iso(first + i * step), "shifted_from": None} for i in range(n)]

RUN_CASES = [
    ("daily 03:41 Asia/Kolkata across 8 Mar", {"preset": "daily", "cron": "41 3 * * *", "tz": "Asia/Kolkata"}, "2026-03-07T00:00:00Z", 3),
    ("daily 03:41 Asia/Kolkata across 1 Nov", {"preset": "daily", "cron": "41 3 * * *", "tz": "Asia/Kolkata"}, "2026-10-31T00:00:00Z", 3),
    ("daily 09:00 America/New_York across 8 Mar (offset changes)", {"preset": "daily", "cron": "0 9 * * *", "tz": "America/New_York"}, "2026-03-07T00:00:00Z", 3),
    ("daily 02:30 America/New_York across 8 Mar (02:30 doesn't exist: runs 03:00)", {"preset": "daily", "cron": "30 2 * * *", "tz": "America/New_York"}, "2026-03-07T12:00:00Z", 3),
    ("daily 09:00 America/New_York across 1 Nov (offset changes)", {"preset": "daily", "cron": "0 9 * * *", "tz": "America/New_York"}, "2026-10-31T00:00:00Z", 3),
    ("daily 01:30 America/New_York across 1 Nov (01:30 happens twice: runs once, the first)", {"preset": "daily", "cron": "30 1 * * *", "tz": "America/New_York"}, "2026-10-31T12:00:00Z", 3),
    ("hourly anchored, America/New_York through fall-back (no gap)", {"preset": "hourly", "interval_min": 60, "anchor_at": "2026-11-01T03:41:00Z", "tz": "America/New_York"}, "2026-11-01T04:00:00Z", 5),
    ("hourly at minute :41 unanchored, America/New_York through fall-back", {"preset": "hourly", "interval_min": 60, "minute": 41, "tz": "America/New_York"}, "2026-11-01T04:00:00Z", 5),
    ("hourly at minute :41 unanchored, Asia/Kolkata (UTC minute 11)", {"preset": "hourly", "interval_min": 60, "minute": 41, "tz": "Asia/Kolkata"}, "2026-10-21T00:00:00Z", 3),
    ("every 6 hours anchored, America/New_York across 8 Mar (even spacing)", {"preset": "every_n_hours", "interval_min": 360, "anchor_at": "2026-03-07T22:11:00Z", "tz": "America/New_York"}, "2026-03-08T00:00:00Z", 5),
    ("weekdays 03:41 Asia/Kolkata from a Friday", {"preset": "weekdays", "cron": "41 3 * * 1-5", "tz": "Asia/Kolkata"}, "2026-10-23T00:00:00Z", 4),
    ("weekly Monday 09:00 Asia/Kolkata", {"preset": "weekly", "cron": "0 9 * * 1", "tz": "Asia/Kolkata"}, "2026-10-20T00:00:00Z", 3),
    ("custom 0 9 1 * 1 in UTC: day 1 OR Monday (day_or)", {"preset": "cron", "cron": "0 9 1 * 1", "tz": "UTC"}, "2026-10-20T00:00:00Z", 4),
    ("custom 17 3 * * * in Asia/Calcutta (alias of Asia/Kolkata)", {"preset": "cron", "cron": "17 3 * * *", "tz": "Asia/Calcutta"}, "2026-10-20T00:00:00Z", 2),
]
REJECT_CASES = [
    ("* * * * *", "tooOften"),
    ("*/5 * * * *", "tooOften"),
    ("0,30 * * * *", "tooOften"),
    ("0 9 L * *", "unsupported"),
    ("0 9 ? * 1", "unsupported"),
    ("0 9 * * 1#2", "unsupported"),
    ("0 9 15W * *", "unsupported"),
    ("0 9 * *", "fields"),
    ("0 0 9 * * 1", "fields"),
    ("0 24 * * *", "range"),
    ("0 9 31 2 *", "never"),
]

def tz_of(c):
    return {"Asia/Calcutta": "Asia/Kolkata"}.get(c["tz"], c["tz"])

cases = []
for name, cad, frm, n in RUN_CASES:
    f = parse_iso(frm)
    if cad["preset"] in ("hourly", "every_n_hours"):
        runs = interval_runs(cad, f, n)
    else:
        runs = cron_runs(cad["cron"], tz_of(cad), f, n)
    cases.append({"name": name, "cadence": cad, "from": frm, "n": n, "expect": runs})
doc = {
    "about": "Next-run times shared by the JS cronModel tests now and the Python scheduler tests later (notify DESIGN 2.2, BUILD-BRIEF 4.0). Times are UTC. shifted_from names a wall time that didn't exist that day.",
    "generated_by": "scripts/generate-next-runs-golden.py (Python zoneinfo, independent of the JS model)",
    "tzdata": "system",
    "runs": cases,
    "rejected": [{"cron": c, "code": code} for c, code in REJECT_CASES],
}
json.dump(doc, sys.stdout, indent=2, ensure_ascii=False)
sys.stdout.write("\n")
