// Run status map (PRODUCT-SPEC §4, C14). Moved from
// sections/develop-detail/ExperimentTab/ExperimentTab.jsx (STATUS_STYLES),
// which now imports it. One status vocabulary for every run surface.
import { palette } from "src/theme/palette";

const P = palette("light");
// Grey (black-based) chips vanish on a dark surface: in dark mode they
// switch to a white tint with a visible border, like the coloured chips
// keep theirs (critic r3 #8: Stopped lost its pill).
const DARK_NEUTRAL = {
  darkBg: "rgba(255, 255, 255, 0.08)",
  darkBorder: "rgba(255, 255, 255, 0.24)",
};

/**
 * Chip styles keyed by the backend status (canonical Run `status`).
 * Existing entries keep their colours and icons; `label` is what users read.
 */
export const STATUS_STYLES = {
  NotStarted: {
    bg: P.black.o10,
    color: P.black["500"],
    border: P.black.o30,
    icon: "/assets/icons/ic_call_pending.svg",
    darkColor: P.black["300"],
    ...DARK_NEUTRAL,
    label: "Not Started",
  },
  Queued: {
    bg: P.orange.o10,
    color: P.orange["700"],
    border: P.orange.o30,
    icon: "/assets/icons/ic_queued_header.svg",
    darkColor: P.orange["300"],
    label: "Queued",
  },
  Running: {
    bg: P.blue.o10,
    color: P.blue["800"],
    border: P.blue.o30,
    icon: "/assets/icons/ic_queued_header.svg",
    darkColor: P.blue["300"],
    label: "Running",
    spin: true,
  },
  ExperimentEvaluation: {
    bg: P.purple.o10,
    color: P.purple["700"],
    border: P.purple.o30,
    icon: "/assets/icons/ic_queued_header.svg",
    darkColor: P.purple["300"],
    label: "Scoring",
    spin: true,
  },
  Completed: {
    bg: P.green.o10,
    color: P.green["500"],
    border: P.green["300"],
    icon: "/assets/icons/ic_completed.svg",
    darkColor: P.green["300"],
    label: "Completed",
  },
  PartialCompleted: {
    bg: P.orange.o10,
    color: P.orange["700"],
    border: P.orange.o30,
    icon: "/assets/icons/ic_warning.svg",
    darkColor: P.orange["300"],
    // the capability contract: a partial run never reads the word Completed.
    label: "Finished with errors",
  },
  Cancelled: {
    bg: P.black.o10,
    color: P.black["600"],
    border: P.black.o30,
    icon: "/assets/icons/ic_stop_v2.svg",
    darkColor: P.black["200"],
    ...DARK_NEUTRAL,
    label: "Stopped",
  },
  Failed: {
    bg: P.red.o10,
    color: P.red["800"],
    border: P.red.o30,
    icon: "/assets/icons/ic_failed.svg",
    darkColor: P.red["300"],
    label: "Failed",
  },
  Editing: {
    bg: P.pink.o10,
    color: P.pink["500"],
    border: P.pink.o30,
    icon: "/assets/icons/ic_edit_pencil.svg",
    darkColor: P.pink["300"],
    label: "Editing",
  },
  WaitingForResults: {
    bg: P.yellow.o10,
    color: P.yellow["800"],
    border: P.yellow.o30,
    icon: "/assets/icons/ic_call_pending.svg",
    darkColor: P.yellow["300"],
    label: "Waiting for results",
  },
};

/** Unknown statuses: their raw text in neutral grey (never green). */
export const UNKNOWN_STATUS_STYLE = {
  bg: P.black.o10,
  color: P.black["500"],
  darkColor: P.black["300"],
  border: P.black.o30,
  ...DARK_NEUTRAL,
  icon: null,
};

/** Statuses that keep changing on their own (poll every 5 s). */
export const ACTIVE_STATUSES = [
  "NotStarted",
  "Queued",
  "Running",
  "ExperimentEvaluation",
];
/** Statuses that never change without a user action. */
export const TERMINAL_STATUSES = [
  "Completed",
  "PartialCompleted",
  "Failed",
  "Cancelled",
];
/** Waiting on external uploads: poll slowly. */
export const WAITING_STATUSES = ["WaitingForResults"];

const LOOKUP = Object.fromEntries(
  Object.keys(STATUS_STYLES).map((k) => [k.toLowerCase(), k]),
);
// Aliases seen on older list endpoints.
Object.assign(LOOKUP, {
  not_started: "NotStarted",
  "not started": "NotStarted",
  in_progress: "Running",
  stopped: "Cancelled",
  canceled: "Cancelled",
  partial: "PartialCompleted",
  partially_completed: "PartialCompleted",
  waiting_for_results: "WaitingForResults",
  evaluating: "ExperimentEvaluation",
  scoring: "ExperimentEvaluation",
  // the capability contract: the backend's own failure and partial words (StatusType
  // ERROR, PARTIAL_RUN) read Failed / Finished with errors, never grey.
  error: "Failed",
  errored: "Failed",
  partialrun: "PartialCompleted",
  partial_run: "PartialCompleted",
});

/** Canonical status key for a raw value, or null when unknown. */
export const normalizeStatus = (raw) =>
  raw == null ? null : LOOKUP[String(raw).trim().toLowerCase()] ?? null;

const count = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Rows of a run that errored: `progress.errors`, else its ledger's errored rows. */
function erroredRowsOfRun(run) {
  const n = count(run?.progress?.errors);
  if (n != null) return n;
  const byCause = run?.ledger?.rows?.errored;
  if (!byCause || typeof byCause !== "object") return 0;
  return Object.values(byCause).reduce((s, v) => s + (Number(v) || 0), 0);
}

/** Rows the run played (their tries are in), else the rows it had. */
const playedRowsOfRun = (run) =>
  count(run?.progress?.done) ||
  count(run?.progress?.total) ||
  count(run?.ledger?.rows?.total) ||
  count(run?.examples) ||
  0;

/**
 * the capability contract: the ONE status key of a run, from its real outcome. A run
 * that ended `Completed` (or `PartialCompleted`) while every row it played
 * errored is `Failed`; one with errored rows is `PartialCompleted`
 * (`Finished with errors`). Every other status is the run's own. Every
 * chip, pill, tray line and ledger status reads this through getRunStatus.
 * @param {object} run  canonical Run (`status`, `progress`, `ledger`)
 * @returns {string|null}  canonical key, or null when the status is unknown
 */
export function runOutcomeKey(run) {
  const key = normalizeStatus(run?.status);
  if (key !== "Completed" && key !== "PartialCompleted") return key;
  const errored = erroredRowsOfRun(run);
  if (errored <= 0) return key;
  const played = playedRowsOfRun(run);
  return played > 0 && errored >= played ? "Failed" : "PartialCompleted";
}

/**
 * Display info for a run or a raw status string. A run is read by its real
 * outcome (runOutcomeKey): a run whose rows errored never reads Completed.
 * @param {object|string} runOrStatus canonical Run or its `status`
 * @returns {{key: string, label: string, color: string, darkColor: string,
 *   bg: string, darkBg: string, darkBorder: string,
 *   border: string, icon: string|null, spin: boolean, active: boolean,
 *   terminal: boolean, waiting: boolean, known: boolean}}
 */
export function getRunStatus(runOrStatus) {
  const isRun = Boolean(runOrStatus) && typeof runOrStatus === "object";
  const raw = isRun ? runOrStatus.status : runOrStatus;
  // the capability contract: a run object reads its real outcome, never its stamp.
  const key = isRun ? runOutcomeKey(runOrStatus) : normalizeStatus(raw);
  if (!key) {
    const text = raw == null || raw === "" ? "Unknown" : String(raw);
    return {
      key: text,
      label: text,
      ...UNKNOWN_STATUS_STYLE,
      spin: false,
      active: false,
      terminal: false,
      waiting: false,
      known: false,
    };
  }
  const s = STATUS_STYLES[key];
  return {
    key,
    label: s.label,
    color: s.color,
    darkColor: s.darkColor ?? s.color,
    bg: s.bg,
    border: s.border,
    darkBg: s.darkBg ?? s.bg,
    darkBorder: s.darkBorder ?? s.border,
    icon: s.icon,
    spin: !!s.spin,
    active: ACTIVE_STATUSES.includes(key),
    terminal: TERMINAL_STATUSES.includes(key),
    waiting: WAITING_STATUSES.includes(key),
    known: true,
  };
}

/**
 * React Query refetchInterval for a set of runs: 5 s while any run is
 * active, 30 s while any waits for uploaded results, otherwise false
 * (polling stops on every terminal status).
 */
// R3 (P3, EL#2): the leaderboard pauses polling while one of its menus,
// popovers or a selection is open, so rows never move under the pointer.
// `pausePolling()` returns its release; pollers re-read the interval on
// their next render.
let pollPausers = 0;
export function pausePolling() {
  pollPausers += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    pollPausers = Math.max(0, pollPausers - 1);
  };
}
export const isPollingPaused = () => pollPausers > 0;

export function pollIntervalFor(runs) {
  if (pollPausers > 0) return false;
  const list = Array.isArray(runs) ? runs : runs ? [runs] : [];
  const infos = list.map(getRunStatus);
  if (infos.some((i) => i.active)) return 5000;
  if (infos.some((i) => i.waiting)) return 30000;
  return false;
}

/** "About 2 min left" from seconds, or null. */
export function etaText(etaSeconds) {
  if (etaSeconds == null || !Number.isFinite(etaSeconds) || etaSeconds < 0)
    return null;
  if (etaSeconds < 60) return "Less than a minute left";
  const min = Math.round(etaSeconds / 60);
  if (min < 60) return `About ${min} min left`;
  const h = Math.round((etaSeconds / 3600) * 10) / 10;
  return `About ${h} h left`;
}

/** `{done, total, errors}` from a run (canonical `progress`), never NaN. */
export function runProgressOf(run) {
  const p = run?.progress ?? {};
  const total = Number.isFinite(p.total) ? p.total : run?.examples ?? 0;
  const done = Number.isFinite(p.done) ? p.done : 0;
  const errors = Number.isFinite(p.errors) ? p.errors : 0;
  const scored = Number.isFinite(p.scored) ? p.scored : null;
  // L7c g5 (ledger:leaderboard-progress): the bar and its count are the
  // rows scored (the run's results), so the bar is never full while rows
  // are still being scored; `done` stays the rows whose tries are in.
  const finished = scored != null ? Math.min(scored, done) : done;
  return {
    done,
    total,
    errors,
    scored,
    finished,
    left: Math.max(0, total - done),
    pct: total > 0 ? Math.min(100, Math.round((finished / total) * 100)) : 0,
    eta_s: p.eta_s ?? null,
  };
}
