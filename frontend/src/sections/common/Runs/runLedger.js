import {
  formatExactMoney,
  isMoneyValue,
  subtractMoneyValues,
  moneySign,
  roundMoneyPercent,
  formatMoneyDelta,
} from "src/sections/common/format/money";
// W1-6 (MUST-BUILD W1-6, W1-SCOPE A8): the ONE run ledger. Every count of
// a run's rows and tries (done, running, queued, scored, skipped by reason,
// N/A, errored by cause, stopped by a limit, carried) comes from ledgerOf,
// and every screen prints ledgerLine of it, never a count of its own:
// R1 returns `ledger` (the mock builds it here), and the run identity line,
// the tray, the waiting panel, the results header and the summary print it.
// The same file words the planned form Review shows before a run exists
// (`plannedLine`), the seeded sample (`sampleLine`) and estimate vs actual
// (`spendLine`). Pure.
import { errorCauseOf, errorTextOf } from "./errorCause";
import { getRunStatus } from "./runStatus";
import { STAND_IN_EVENTS_BY_LABEL } from "src/sections/common/Connect/standInEvents";

/**
 * L7c g5 (sdk:waiting-copy): the ONE waiting noun. Every surface that says
 * what a waiting run waits for (tray pill and dock, status chip, waiting
 * banner, results footer, cells) prints it from here: an upload run waits
 * for `results` (the run status's own word, `Waiting for results`), never
 * for "your code". Pure.
 */
export const WAIT_NOUNS = Object.freeze({
  upload: "results",
  ci: "the CI job",
  local: "your terminal",
  sandbox: "the sandbox",
});

/**
 * L7c g5 (standin:runbar:simulate-by-mode): what each Simulate stand-in
 * stands in for, by its label. Every door that offers one (waiting banner,
 * run row menu, RunBar chip menu) tags it with these words: the one
 * machine names of Connect/standInEvents.js (L7c merge).
 */
export const STAND_IN_EVENTS = STAND_IN_EVENTS_BY_LABEL;

/** How a waiting run waits: upload | ci | local | sandbox. Pure. */
export function waitModeOf(run = {}) {
  const cfg = run?.config ?? {};
  const kind =
    cfg.kind ??
    run?.kind ??
    (run?.target_type === "external" ? "upload" : "code");
  if (kind === "upload") return "upload";
  const mode = cfg.execution?.mode;
  if (mode === "sandbox" || mode === "ci") return mode;
  return mode === "upload" ? "upload" : "local";
}

/** `results` / `the CI job` / `your terminal` / `the sandbox`. Pure. */
export const waitNounOf = (run = {}) => WAIT_NOUNS[waitModeOf(run)];

/** `waiting for results`: the one waiting phrase. Pure. */
export const waitingPhraseOf = (run = {}) => `waiting for ${waitNounOf(run)}`;

/**
 * L7c g5 (status:leaderboard-errors): errored rows of a run from its ledger
 * (every cause), so the leaderboard's `Errors · rows` and the Summary's
 * column count the same thing; `fallback` when the run has no ledger.
 */
export function erroredRowsOf(run, fallback = null) {
  const e = run?.ledger?.rows?.errored;
  if (!e || typeof e !== "object") return fallback;
  return Object.values(e).reduce((s, n) => s + (Number(n) || 0), 0);
}

/** A run that waits for results from outside (status WaitingForResults). */
export const isWaitingRun = (run) =>
  Boolean(run?.status) && getRunStatus(run).waiting;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const finished = (r) =>
  r?.visibility === "completed" || r?.visibility === "error";
const waiting = (r) => r?.visibility === "queued";
const playing = (r) => r?.visibility === "running";
// W7-4 (H-A4): a finished try whose primary eval is a remote scorer still
// waiting for its answer (`typed.pending`) is counted as running.
const scoringTyped = (r, key) =>
  Boolean(key && r?.scores?.[key]?.typed?.pending);
// A try a run limit stopped before it ran (the cost cap, a time limit):
// the engine and the hook-filled runs record it as `attempt_status:
// "stopped"`. A per-try cap's stop is the try's own result, not a run limit.
const stoppedTry = (r) => r?.attempt_status === "stopped" && !r?.try_cap;
// Review M2: a try that never ran for a reason of its own (`status:
// "infra"`, e.g. STORY's T13 trial 2 "Setup failed: image pull timed out")
// is left out of scores with its cause, never "stopped by a limit".
const excludedTry = (r) =>
  !stoppedTry(r) && (r?.status === "infra" || r?.trial_outcome === "infra");
/** The cause words of a try left out of scores (`setup failed`). */
const excludedCauseOf = (r) =>
  errorTextOf(r)
    ? errorCauseOf({ ...r, status: "error" }) ?? "did not run"
    : "did not run";

/** The limit a run stopped at, from its stop reason: cost_cap | time_limit | max_turns | null. */
export function stopKindOf(reason) {
  const t = String(reason ?? "");
  if (!t) return null;
  if (/cost cap/i.test(t)) return "cost_cap";
  if (/time limit|timed out|timeout/i.test(t)) return "time_limit";
  if (/max(?:imum)? turns/i.test(t)) return "max_turns";
  return "stopped";
}

export const STOP_WORDS = Object.freeze({
  cost_cap: "stopped at cost cap",
  time_limit: "stopped at time limit",
  max_turns: "stopped at max turns",
  stopped: "stopped",
});

const bump = (obj, key) => {
  obj[key] = (obj[key] ?? 0) + 1;
};

/**
 * The ledger of one run from its tries as R1 / R3 see them at `now`
 * (`listRunResults(..., {includeQueued: true})`: each try with `visibility`
 * completed | error | running | queued).
 *
 * A row is counted once, in the first bucket that fits: stopped (a run
 * limit stopped every try before it ran; a stopped try is never queued),
 * queued (no try has started), running (some try is still playing or
 * queued), errored (every finished try
 * errored; by cause, errorCause), then by its primary eval: scored (a try
 * has a value), N/A, skipped (by the reason its cell names), not scored
 * (no cell yet). A run with no eval counts finished rows as `ran`.
 * `carried` counts rows copied from the run it was started from; they are
 * also in the bucket their tries fall in.
 *
 * @param {{records?: object[], primaryKey?: string|null,
 *   stopReason?: string|null}} p
 * @returns {{rows: object, tries: object, stopped_by: string|null,
 *   primary_key: string|null}}
 */
export function ledgerOf({
  records = [],
  primaryKey = null,
  stopReason = null,
  atOnce = null,
  active = false,
} = {}) {
  const byRow = new Map();
  records.forEach((r) => {
    if (!r) return;
    const id = r.example_id ?? r.row_id;
    if (!byRow.has(id)) byRow.set(id, []);
    byRow.get(id).push(r);
  });
  const rows = {
    total: byRow.size,
    done: 0,
    running: 0,
    queued: 0,
    scored: 0,
    ran: 0,
    unscored: 0,
    na: 0,
    skipped: {},
    errored: {},
    excluded: {},
    stopped: 0,
    carried: 0,
  };
  // The rows each bucket holds (the filter its number opens: a `rows` pill).
  const ids = {
    scored: [],
    ran: [],
    unscored: [],
    na: [],
    skipped: {},
    errored: {},
    excluded: {},
    stopped: [],
    running: [],
    queued: [],
    carried: [],
  };
  const tries = {
    total: records.length,
    done: 0,
    running: 0,
    queued: 0,
    scored: 0,
    na: 0,
    skipped: 0,
    errored: 0,
    excluded: 0,
    excluded_by: {},
    // tries left out on rows whose other tries ran, by cause
    left_out: {},
    stopped: 0,
  };
  records.forEach((r) => {
    // A try a limit stopped never plays: it is stopped, never queued.
    if (stoppedTry(r)) {
      tries.stopped += 1;
      return;
    }
    if (excludedTry(r)) {
      tries.excluded += 1;
      bump(tries.excluded_by, excludedCauseOf(r));
      return;
    }
    if (waiting(r)) tries.queued += 1;
    else if (playing(r)) tries.running += 1;
    if (!finished(r)) return;
    if (scoringTyped(r, primaryKey)) {
      tries.running += 1;
      return;
    }
    tries.done += 1;
    if (r.status === "error") {
      tries.errored += 1;
      return;
    }
    const cell = primaryKey ? r.scores?.[primaryKey] : null;
    if (isNum(cell?.value)) tries.scored += 1;
    else if (cell?.status === "na") tries.na += 1;
    else if (cell?.status === "skipped") tries.skipped += 1;
  });
  const into = (bucket, id, key) => {
    if (key == null) {
      ids[bucket].push(id);
      return;
    }
    ids[bucket][key] = [...(ids[bucket][key] ?? []), id];
  };
  byRow.forEach((recs, id) => {
    if (recs.some((r) => r.carried_from || r.carried_from_run_id)) {
      rows.carried += 1;
      into("carried", id);
    }
    const live = recs.filter((r) => !stoppedTry(r));
    if (!live.length) {
      rows.stopped += 1;
      into("stopped", id);
      return;
    }
    if (live.every(waiting)) {
      rows.queued += 1;
      into("queued", id);
      return;
    }
    if (
      live.some((r) => playing(r) || waiting(r) || scoringTyped(r, primaryKey))
    ) {
      // L7c g5: with the run's At once known, a row whose every try is in
      // but not scored yet is `scoring`; a row with tries still to play is
      // in progress (`partial`, placed below).
      if (atOnce != null && !live.some(waiting)) {
        rows.scoring = (rows.scoring ?? 0) + 1;
        (ids.scoring ??= []).push(id);
        return;
      }
      if (atOnce != null) {
        (ids.partial ??= []).push(id);
        return;
      }
      rows.running += 1;
      into("running", id);
      return;
    }
    const ran = live.filter((r) => finished(r) && !excludedTry(r));
    rows.done += 1;
    if (ran.length)
      live
        .filter(excludedTry)
        .forEach((r) => bump(tries.left_out, excludedCauseOf(r)));
    // Every try of the row was left out (none ran): excluded, by cause.
    if (!ran.length) {
      const cause = excludedCauseOf(live.find(excludedTry) ?? live[0]);
      bump(rows.excluded, cause);
      into("excluded", id, cause);
      return;
    }
    if (ran.every((r) => r.status === "error")) {
      const cause = errorCauseOf(ran[0]) ?? "other error";
      bump(rows.errored, cause);
      into("errored", id, cause);
      return;
    }
    // L7c g5 (ledger:r1): a try that errored on a row whose other tries
    // ran is counted at try level, with its cause (TS #4A's 18 errored
    // tries), never silently folded into the row's score.
    ran
      .filter((r) => r.status === "error")
      .forEach((r) =>
        bump((tries.errored_in_rows ??= {}), errorCauseOf(r) ?? "other error"),
      );
    if (!primaryKey) {
      rows.ran += 1;
      into("ran", id);
      return;
    }
    const cells = ran
      .filter((r) => r.status !== "error")
      .map((r) => r.scores?.[primaryKey])
      .filter(Boolean);
    if (cells.some((c) => isNum(c.value))) {
      rows.scored += 1;
      into("scored", id);
    } else if (cells.some((c) => c.status === "na")) {
      rows.na += 1;
      into("na", id);
    } else if (cells.some((c) => c.status === "skipped")) {
      const reason =
        cells.find((c) => c.status === "skipped").skip_reason ?? "skipped";
      bump(rows.skipped, reason);
      into("skipped", id, reason);
    } else {
      rows.unscored += 1;
      into("unscored", id);
    }
  });
  // L7c g5 (ledger:leaderboard-progress): `running` is the rows in flight,
  // never more than the run's At once: rows with tries left to play first,
  // then the next queued rows, only while the run is active.
  if (atOnce != null) {
    const cap = Math.max(1, Math.round(Number(atOnce) || 1));
    const partial = ids.partial ?? [];
    delete ids.partial;
    const order = [...partial, ...ids.queued];
    const inFlight = active ? order.slice(0, cap) : [];
    const fly = new Set(inFlight);
    rows.running = inFlight.length;
    ids.running = inFlight;
    ids.queued = order.filter((x) => !fly.has(x));
    rows.queued = ids.queued.length;
    rows.scoring ??= 0;
    ids.scoring ??= [];
  }
  return {
    rows,
    tries,
    ...(atOnce != null
      ? { at_once: Math.max(1, Math.round(Number(atOnce) || 1)) }
      : {}),
    stopped_by:
      rows.stopped || tries.stopped
        ? stopKindOf(stopReason) ?? "stopped"
        : null,
    primary_key: primaryKey,
    row_ids: ids,
  };
}

/**
 * Review S1: what a run's rows are called on every screen: `task` when the
 * run's primary eval is Task success (a task suite), else `row`. A screen
 * that knows the dataset's kind may pass `noun` itself; the two agree.
 */
export function ledgerNounOf(ledger) {
  return ledger?.primary_key === "eval:task_success" ? "task" : "row";
}

const addInto = (a, b) => {
  Object.entries(b ?? {}).forEach(([k, v]) => {
    if (typeof v === "number") a[k] = (a[k] ?? 0) + v;
    else if (Array.isArray(v)) a[k] = [...(a[k] ?? []), ...v];
    else if (v && typeof v === "object") a[k] = addInto({ ...(a[k] ?? {}) }, v);
  });
  return a;
};

/**
 * Several runs' ledgers as one (a create that launched two runs): every
 * bucket summed (a row counts once per run), the stop kind of the first
 * run a limit stopped. Its rows are per run; screens say how many runs.
 */
export function mergeLedgers(ledgers) {
  const list = (ledgers ?? []).filter((l) => l?.rows);
  if (list.length === 1) return list[0];
  const rows = {};
  const tries = {};
  list.forEach((l) => {
    addInto(rows, l.rows);
    addInto(tries, l.tries);
  });
  const keys = new Set(list.map((l) => l.primary_key));
  return {
    rows,
    tries,
    stopped_by: list.find((l) => l.stopped_by)?.stopped_by ?? null,
    primary_key: keys.size === 1 ? [...keys][0] : null,
  };
}

const sumOf = (obj) => Object.values(obj ?? {}).reduce((s, n) => s + n, 0);
// `4 no expected value` or `RateLimitError 2`, most first.
const byCount = (obj) =>
  Object.entries(obj ?? {}).sort(
    (a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1),
  );

function reasonsText(obj, { countFirst }) {
  const list = byCount(obj);
  if (list.length === 1) return list[0][0];
  return list
    .map(([k, n]) => (countFirst ? `${n} ${k}` : `${k} ${n}`))
    .join(", ");
}

/**
 * The parts of a ledger's sentence, in reading order. Each carries the
 * filter its number opens (`filter`: `{type: "error"}`, `{type:
 * "skipped"}`, `{type: "na"}`, `{type: "not_run"}`, `{type: "carried"}`)
 * and, when the ledger lists them (R1 `row_ids`), the exact rows (`ids`)
 * and a pill label (`label`) a screen opens as a `rows` pill; an errored
 * part also names its `cause` when it has one.
 * @param {object} ledger  ledgerOf(...)
 * @param {{noun?: string, carriedFrom?: string|null, stopWords?: boolean}} o
 *   `carriedFrom` names the run carried rows came from (`208 carried from
 *   #2A`); `stopWords: false` drops the trailing `· stopped at cost cap`
 *   (a title that already says why the run stopped).
 * @returns {Array<{key: string, n: number, text: string, filter: object|null,
 *   ids?: string[], label?: string, cause?: string}>}
 */
export function ledgerParts(
  ledger,
  {
    noun: nounOpt = null,
    carriedFrom = null,
    stopWords = true,
    waitingFor = null,
    stoppedRun = false,
  } = {},
) {
  if (!ledger?.rows) return [];
  const noun = nounOpt ?? ledgerNounOf(ledger);
  const r = ledger.rows;
  const ids = ledger.row_ids ?? null;
  const nouns = (n) => (n === 1 ? noun : `${noun}s`);
  const flat = (obj) => Object.values(obj ?? {}).flat();
  const parts = [];
  const push = (key, n, text, filter = null, extra = {}) => {
    if (n > 0) parts.push({ key, n, text, filter, ...extra });
  };
  const rowsOf = (list, label) =>
    ids && Array.isArray(list) && list.length ? { ids: list, label } : {};
  const Noun = noun.charAt(0).toUpperCase() + noun.slice(1);
  push(
    "scored",
    r.scored,
    `${r.scored} scored`,
    null,
    rowsOf(ids?.scored, `Scored ${nouns(2)}`),
  );
  push(
    "ran",
    r.ran,
    `${r.ran} ran`,
    null,
    rowsOf(ids?.ran, `${Noun}s that ran`),
  );
  const skipped = sumOf(r.skipped);
  const skipReasons = byCount(r.skipped);
  push(
    "skipped",
    skipped,
    `${skipped} skipped (${reasonsText(r.skipped, { countFirst: true })})`,
    { type: "skipped" },
    rowsOf(
      flat(ids?.skipped),
      skipReasons.length === 1
        ? `Skipped · ${skipReasons[0][0]}`
        : `Skipped ${nouns(2)}`,
    ),
  );
  push("na", r.na, `${r.na} N/A`, { type: "na" }, rowsOf(ids?.na, "N/A"));
  push(
    "unscored",
    r.unscored,
    `${r.unscored} not scored yet`,
    null,
    rowsOf(ids?.unscored, "Not scored yet"),
  );
  const errored = sumOf(r.errored);
  const causes = byCount(r.errored);
  push(
    "errored",
    errored,
    `${errored} errored (${reasonsText(r.errored, { countFirst: false })})`,
    { type: "error" },
    {
      ...rowsOf(flat(ids?.errored), "Errored"),
      ...(causes.length === 1 ? { cause: causes[0][0] } : {}),
      // each cause with its rows (a screen opens one cause's rows)
      causes: causes.map(([cause, n]) => ({
        cause,
        n,
        ...(ids?.errored?.[cause] ? { ids: ids.errored[cause] } : {}),
      })),
    },
  );
  const excluded = sumOf(r.excluded);
  push(
    "excluded",
    excluded,
    `${excluded} left out (${reasonsText(r.excluded, { countFirst: false })})`,
    { type: "excluded" },
    rowsOf(flat(ids?.excluded), "Left out of scores"),
  );
  push(
    "running",
    r.running,
    `${r.running} running`,
    null,
    rowsOf(ids?.running, "Running"),
  );
  push(
    "scoring",
    r.scoring ?? 0,
    `${r.scoring} scoring`,
    null,
    rowsOf(ids?.scoring, "Scoring"),
  );
  // L7c g5: a run waiting for results says what its rows wait for (the
  // one waiting noun), never that they are queued.
  // L7c merge (status:ledger, g6 owed to g5): a stopped run's unplayed
  // rows will never play, so they read `not run · stopped`, never queued.
  if (stoppedRun && !waitingFor)
    push(
      "queued",
      r.queued,
      `${r.queued} not run · stopped`,
      { type: "not_run" },
      rowsOf(ids?.queued, "Not run"),
    );
  else
    push(
      "queued",
      r.queued,
      waitingFor
        ? `${r.queued} waiting for ${waitingFor}`
        : `${r.queued} queued`,
      null,
      rowsOf(ids?.queued, waitingFor ? "Waiting" : "Queued"),
    );
  const stop = STOP_WORDS[ledger.stopped_by] ?? "stopped";
  push(
    "stopped",
    r.stopped,
    stopWords ? `${r.stopped} not run · ${stop}` : `${r.stopped} not run`,
    { type: "not_run" },
    rowsOf(ids?.stopped, "Not run"),
  );
  if (!r.stopped && ledger.stopped_by && stopWords)
    parts.push({ key: "stop", n: 0, text: stop, filter: null });
  if (parts.length && parts[0].n > 0)
    parts[0] = {
      ...parts[0],
      text: parts[0].text.replace(
        /^(\d+) /,
        (m, n) => `${n} ${nouns(Number(n))} `,
      ),
    };
  // A try left out on a row whose other tries ran (T13's setup failure on
  // trial 2): said once, at try level, with its cause.
  const loose = ledger.tries?.left_out ?? null;
  const looseN = sumOf(loose);
  const erroredIn = ledger.tries?.errored_in_rows ?? null;
  const erroredInN = sumOf(erroredIn);
  if (erroredInN > 0)
    parts.push({
      key: "errored_tries",
      n: erroredInN,
      text: `${erroredInN} ${erroredInN === 1 ? "try" : "tries"} errored (${reasonsText(erroredIn, { countFirst: false })})`,
      filter: { type: "error" },
    });
  if (looseN > 0)
    parts.push({
      key: "excluded_tries",
      n: looseN,
      text: `${looseN} ${looseN === 1 ? "try" : "tries"} left out (${reasonsText(loose, { countFirst: false })})`,
      filter: null,
    });
  if (r.carried)
    parts.push({
      key: "carried",
      n: r.carried,
      text: `${r.carried} carried${carriedFrom ? ` from ${carriedFrom}` : ""}`,
      filter: { type: "carried" },
      ...rowsOf(
        ids?.carried,
        carriedFrom ? `Carried from ${carriedFrom}` : "Carried",
      ),
    });
  return parts;
}

/**
 * The one sentence every screen prints for a run:
 * `16 rows scored · 4 skipped (no expected value) · 20 not run · stopped at
 * cost cap`; `180 rows scored · 2 errored (RateLimitError) · 56 running`.
 * Empty when the run has no rows.
 */
export function ledgerLine(ledger, opts = {}) {
  return ledgerParts(ledger, opts)
    .map((p) => p.text)
    .join(" · ");
}

/** `25 random rows (seed 7) of 248`; null without a sample. */
export function sampleLine(sample, { noun = "row" } = {}) {
  const n = Number(sample?.n);
  if (!Number.isFinite(n) || n <= 0) return null;
  const of = Number(sample?.of);
  const picked = Number.isFinite(of) && of > 0 ? Math.min(n, of) : n;
  return `${picked} random ${picked === 1 ? noun : `${noun}s`} (seed ${sample.seed ?? 0})${Number.isFinite(of) && of > 0 ? ` of ${of}` : ""}`;
}

/** The rows a run with a sample plays: min(n, rows in scope). */
export function sampledCount(sample, inScope) {
  const n = Number(sample?.n);
  const total = Number(inScope);
  if (!Number.isFinite(n) || n <= 0) return inScope;
  return Number.isFinite(total) && total >= 0 ? Math.min(n, total) : n;
}

/**
 * Review's planned form of the ledger, per run: `smoke 20 rows × 3 tries =
 * 60 tries`; one try per row reads `20 rows × 1 try = 20 tries`.
 */
export function plannedLine({ rows, tries = 1, scope = null, noun = "row" }) {
  const r = Math.max(0, Math.round(Number(rows) || 0));
  const t = Math.max(1, Math.round(Number(tries) || 1));
  const rowsWord = `${r} ${r === 1 ? noun : `${noun}s`}`;
  return `${scope ? `${scope} ` : ""}${rowsWord} × ${t} ${t === 1 ? "try" : "tries"} = ${r * t} ${r * t === 1 ? "try" : "tries"}`;
}

const usd = (v) => {
  const a = Math.abs(Number(v));
  return formatExactMoney(v, {
    minimumFractionDigits: a < 0.001 ? 0 : a < 0.1 ? 3 : 2,
  });
};

/**
 * Estimate vs actual cost: `Estimated $0.065 · spent $0.066 (+1%) · 40
 * tries`. While the run plays: `Estimated $0.065 · spent $0.030 so far · 18
 * of 40 tries`. Null without an estimate. Time is not compared (the
 * estimator's minutes and the engine's clock use different latency tables
 * until owner decision 3).
 * @param {{estimate?: {usd?: number, tries?: number}|null,
 *   actual?: {usd?: number, tries?: number, done?: boolean}|null}} p
 */
export function spendLine({ estimate, actual } = {}) {
  if (!estimate || !isMoneyValue(estimate.usd)) return null;
  const spent = isMoneyValue(actual?.usd) ? actual.usd : null;
  const triesDone = Number(actual?.tries) || 0;
  const plannedTries = Number(estimate.tries) || 0;
  if (!actual?.done) {
    return `Estimated ${usd(estimate.usd)} · spent ${usd(spent)} so far · ${triesDone} of ${plannedTries} tries`;
  }
  const pct =
    moneySign(estimate.usd) === 1 && spent != null
      ? roundMoneyPercent(
          subtractMoneyValues(spent, estimate.usd),
          estimate.usd,
        )
      : null;
  const delta =
    pct == null
      ? ""
      : ` (${formatMoneyDelta(pct, { prefix: "", minimumFractionDigits: 0 })}%)`;
  return `Estimated ${usd(estimate.usd)} · spent ${usd(spent)}${delta} · ${triesDone} ${triesDone === 1 ? "try" : "tries"}`;
}

/**
 * A run's ledger parts with what the run itself names: the run its carried
 * rows came from (R1 `started_from`, `208 carried from #2A`).
 */
export function runLedgerParts(run, opts = {}) {
  return ledgerParts(run?.ledger, {
    carriedFrom: run?.started_from?.label ?? null,
    waitingFor: isWaitingRun(run) ? waitNounOf(run) : null,
    stoppedRun: getRunStatus(run).key === "Cancelled",
    ...opts,
  });
}

/** runLedgerParts as the one sentence. */
export function runLedgerLine(run, opts = {}) {
  return runLedgerParts(run, opts)
    .map((p) => p.text)
    .join(" · ");
}

/**
 * Review M3: what an actual spend is made of, one number split in two:
 * `$0.73 spent: $0.13 tries + $0.60 judge calls`; `$0.13 spent on tries`
 * when no judge was called. Null without `actual`.
 * @param {{usd?: number, try_usd?: number, judge_usd?: number}|null} actual
 */
export function spendSplitText(actual) {
  if (!actual || !isMoneyValue(actual.usd)) return null;
  const judge = isMoneyValue(actual.judge_usd) ? actual.judge_usd : 0;
  const tries = isMoneyValue(actual.try_usd)
    ? actual.try_usd
    : subtractMoneyValues(actual.usd, judge);
  if (!(judge > 0)) return `${usd(actual.usd)} spent on tries`;
  return `${usd(actual.usd)} spent: ${usd(tries)} tries + ${usd(judge)} judge calls`;
}

/**
 * The counts a screen prints for a run (tray, run details): its ledger
 * line, else `<done> of <total>` from R1 progress (a backend that does not
 * send `ledger` yet).
 */
export function runCountsText(run, { noun = null } = {}) {
  const line = runLedgerLine(run, { noun });
  if (line) return line;
  const p = run?.progress;
  return p?.total ? `${p.done} of ${p.total}` : "";
}

/**
 * Review's planned ledger over every run (W1-8 "Review's math"): each run's
 * rows × its own tries (an endpoint card's or a code card's own tries, else
 * the experiment's Trials), then the total:
 * `#4A smoke 20 rows × 3 tries = 60 tries · #4B smoke 20 rows × 1 try = 20
 * tries · 80 tries in all`. Null without rows.
 * @param {Array<{label: string, tries: number}>} runs
 * @param {{rows: number, scope?: string|null, noun?: string}} o
 */
export function plannedRunsLine(
  runs,
  { rows, scope = null, noun = "row" } = {},
) {
  const r = Math.round(Number(rows) || 0);
  if (!r || !(runs ?? []).length) return null;
  const lines = runs.map(
    (x) =>
      `${x.label} ${plannedLine({ rows: r, tries: x.tries, scope, noun })}`,
  );
  if (runs.length === 1) return lines[0];
  const total = runs.reduce(
    (s, x) => s + r * Math.max(1, Math.round(Number(x.tries) || 1)),
    0,
  );
  return `${lines.join(" · ")} · ${total} tries in all`;
}

/**
 * W1-8 (one flaky count): the run a results status count is about, named
 * when several runs are in view (`6 flaky on #3E`; the Matrix pill counts
 * rows flaky on any run in view, so the two read the same rule over named
 * scopes). Null on a one-run page.
 */
export function statusScopeOf(summary, subjectId, multiRun) {
  if (!multiRun || !subjectId) return null;
  return (
    (summary?.runs ?? []).find((r) => r.run_id === subjectId)?.label ?? null
  );
}
