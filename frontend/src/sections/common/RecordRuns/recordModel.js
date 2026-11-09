// K-2 (MUST-BUILD; GAP-ALL RT-09): Record on the leaderboard. A run started
// with Record off stays off the leaderboard, is reachable by its link, and
// is deleted a fixed time after it starts unless someone records it. The
// mock world imports this module (one rule for the create path, the sweep
// and the footer). Pure.
import { runLabel } from "src/sections/common/Runs/runLabel";

/**
 * Hours an unrecorded run is kept. Source: PX's Record toggle ("unrecorded
 * runs deleted after 24 h"), cited by MUST-BUILD K-2 and GAP-ALL RT-09.
 */
export const UNRECORDED_RETENTION_HOURS = 24;
const HOUR_MS = 3600 * 1000;
export const UNRECORDED_RETENTION_MS = UNRECORDED_RETENTION_HOURS * HOUR_MS;

export const RECORD_LABEL = "Record on the leaderboard";
export const RECORD_HELP = `Off: the run stays off the leaderboard and is deleted ${UNRECORDED_RETENTION_HOURS} hours after it starts unless you record it.`;
export const NOT_RECORDED_HELP = `These runs stay off the leaderboard and are deleted ${UNRECORDED_RETENTION_HOURS} hours after they start unless you record them.`;
/** The Review step's Settings summary line while Record is off. */
export const NOT_RECORDED_SUMMARY = `Not recorded · off the leaderboard, deleted ${UNRECORDED_RETENTION_HOURS} hours after it starts`;

/** The create body's `record` (default true: only `false` turns it off). */
export const recordsOf = (body) => body?.record !== false;

/**
 * Run fields a create writes on each new run.
 * @param {{record?: boolean}|null} body  the create body
 * @param {number} createdAtMs  the run's start
 * @returns {{listed: boolean, recorded: boolean, expires_at: string|null}}
 */
export function recordFieldsOf(body, createdAtMs) {
  if (recordsOf(body))
    return { listed: true, recorded: true, expires_at: null };
  return {
    listed: false,
    recorded: false,
    expires_at: new Date(createdAtMs + UNRECORDED_RETENTION_MS).toISOString(),
  };
}

const msOf = (iso) => {
  const t = Date.parse(iso ?? "");
  return Number.isFinite(t) ? t : null;
};

/**
 * A quick test (or a replay) the Iterate dock started: off the leaderboard
 * by design, with its own footer link. The same test as `quickTestsOf`
 * (iterate/useQuickTests.js), which this pure module cannot import (it
 * pulls in axios); kh-c-recordModel.test.js checks the two agree.
 */
export const isQuickTestRun = (run) =>
  run?.kind === "quick_test" || run?.source?.kind === "quick_test";

/**
 * A run started with Record off and not recorded since: off the leaderboard,
 * `recorded: false`, and not a quick test (owner decision 1: quick tests are
 * never listed here, never recorded and never swept).
 */
export const isUnrecorded = (run) =>
  run?.listed === false && run?.recorded === false && !isQuickTestRun(run);

/** True when an unrecorded run is due for deletion at `nowMs`. */
export function isExpired(run, nowMs) {
  if (!isUnrecorded(run)) return false;
  const at = msOf(run.expires_at);
  return at != null && at <= nowMs;
}

/** Runs started with Record off and not recorded since (quick tests excluded). */
export const notRecordedOf = (runs) => (runs ?? []).filter(isUnrecorded);

/**
 * K-2 (M-C7): the record state of a run started from another one (Test a
 * fix, a quick test, a replay). It never inherits Record off or the parent's
 * deletion time: a listed child is on the leaderboard, so it is recorded; an
 * unlisted one (a quick test) carries no record state at all. Runs without
 * record state (every seeded run) pass through unchanged.
 * @param {object} parentCopy  the parent record the child is copied from
 * @param {{listed: boolean}} child
 * @returns {object} the copy without the parent's record state
 */
export function childRecordBaseOf(parentCopy, { listed }) {
  const { recorded, expires_at: _expires, ...rest } = parentCopy ?? {};
  return listed && typeof recorded === "boolean"
    ? { ...rest, recorded: true, expires_at: null }
    : rest;
}

const pad = (n) => String(n).padStart(2, "0");
const dayKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/**
 * `deleted at 14:05 tomorrow` (local time; `today`, `tomorrow`, else
 * `on Oct 25`); `deleted on the next refresh` once due; null without a time.
 */
export function expiryText(run, nowMs) {
  const at = msOf(run?.expires_at);
  if (at == null) return null;
  if (at <= nowMs) return "deleted on the next refresh";
  const d = new Date(at);
  const now = new Date(nowMs);
  const next = new Date(nowMs);
  next.setDate(next.getDate() + 1);
  const day =
    dayKey(d) === dayKey(now)
      ? "today"
      : dayKey(d) === dayKey(next)
        ? "tomorrow"
        : `on ${MONTHS[d.getMonth()]} ${d.getDate()}`;
  return `deleted at ${pad(d.getHours())}:${pad(d.getMinutes())} ${day}`;
}

/** `#9A recorded · now on the leaderboard`. */
export const recordedText = (label) =>
  `${label || "The run"} recorded · now on the leaderboard`;

/** `1 not recorded`. */
export const notRecordedCountText = (n) => `${n} not recorded`;

/** `#9A · Refund agent · deleted at 14:05 tomorrow` (the footer popover row). */
export function notRecordedRowText(run, nowMs) {
  const name = run?.name || run?.experiment_name || null;
  return [runLabel(run), name, expiryText(run, nowMs)]
    .filter(Boolean)
    .join(" · ");
}
