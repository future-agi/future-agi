// Pure helpers for experiment review (PRODUCT-SPEC §3.11 Review tab, §3.12,
// C7-C9): queue matching, blind sides, the "Regressions first" sort, queue
// names, the win-rate sentence and the pairwise vocabulary (A = run_a = the
// baseline side, B = run_b = the candidate). No React, no I/O.

export const REVIEW_KIND = "experiment_review";
export const MODES = ["pairwise", "rubric"];
export const ORDERS = [
  { value: "regressions", label: "Regressions first" },
  { value: "dataset", label: "Dataset order" },
  { value: "random", label: "Random" },
];

// FNV-1a 32-bit; stable across browsers (seeded shuffles, blind sides).
export function hash32(text) {
  let h = 0x811c9dc5;
  const s = String(text ?? "");
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/** Which side ("A" | "B") shows on the left for a blind row; seed = example + user. */
export const blindLeft = (seed) => (hash32(seed) % 2 ? "B" : "A");

/** Deterministic Fisher-Yates shuffle seeded by `seed` (the queue name). */
export function seededShuffle(list, seed) {
  const out = [...(list ?? [])];
  let state = hash32(seed) || 1;
  const next = () => {
    // mulberry32
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(next() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

const samePair = (m, a, b) =>
  [m?.run_a, m?.run_b].sort().join() === [a, b].sort().join();

/** The queue reviewing a pair (either order) or one run; newest wins. */
export function reviewQueueFor(queues, { mode, runA, runB, runId }) {
  return matchingReviewQueues(queues, { mode, runA, runB, runId })[0] ?? null;
}

/** Queues `Add to existing` may target: same mode and the same runs. */
export function matchingReviewQueues(queues, { mode, runA, runB, runId }) {
  return (queues ?? [])
    .filter((q) => q?.metadata?.kind === REVIEW_KIND)
    .filter((q) => q.metadata.mode === mode)
    .filter((q) =>
      mode === "pairwise"
        ? Boolean(runA && runB) && samePair(q.metadata, runA, runB)
        : Boolean(runId) && q.metadata.run_id === runId,
    )
    .sort((a, b) =>
      String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")),
    );
}

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
/** `Oct 14` — the one short-date format of the queues list (RW#21). */
export const shortDate = (date) => {
  const d = date instanceof Date ? date : new Date(date);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
};

/** `Review · #4A vs #3A · Oct 14` (pairwise) or `Quality · #4A · Oct 14`. */
export function defaultQueueName(mode, { labelA, labelB, label }, date) {
  const day = shortDate(date ?? new Date());
  return mode === "pairwise"
    ? `Review · ${labelB} vs ${labelA} · ${day}`
    : `Quality · ${label} · ${day}`;
}

/**
 * The metric "Regressions first" sorts by: the metric of a movement pill in
 * the current filters, else Task completion when the view has it (agent
 * pairs), else the first eval metric of the view.
 */
export function primaryMetricKey(metrics, filters) {
  const pill = (filters ?? []).find(
    (p) => p?.type === "movement" || p?.type === "fails",
  );
  if (pill?.metric) return pill.metric;
  const keys = (metrics ?? []).map((m) => (typeof m === "string" ? m : m.key));
  if (keys.includes("eval:task_completion")) return "eval:task_completion";
  return keys.find((k) => String(k).startsWith("eval:")) ?? keys[0] ?? null;
}

/** R3 sort for an Order choice (§3.12 submit). */
export function orderSort(order, { metric, runB, mode = "pairwise" }) {
  if (order === "regressions" && metric && runB)
    // A rubric has no baseline in view: its worst rows come first instead.
    return mode === "rubric"
      ? `score:${metric}:${runB}:asc`
      : `delta:${metric}:${runB}:asc`;
  return "order";
}

/** Items for add-items: `{source_type, source_id, metadata}` per example. */
export function reviewItems(
  exampleIds,
  { mode, runA, runB, runId, blind, seed },
) {
  return (exampleIds ?? []).map((id) => ({
    source_type: "dataset_row",
    source_id: id,
    metadata:
      mode === "pairwise"
        ? {
            kind: REVIEW_KIND,
            mode,
            run_a: runA,
            run_b: runB,
            left: blind ? blindLeft(`${seed}|${id}`) : "A",
            blind: Boolean(blind),
          }
        : { kind: REVIEW_KIND, mode, run_id: runId },
  }));
}

/**
 * Queue metadata for a new review queue. W4 C1 (additive): `corrections`
 * `{on, column_id, column_name}` adds `corrections: {column_id, column_name}`
 * when reviewers may correct the expected answer.
 */
export function queueMetadata({
  mode,
  runA,
  runB,
  runId,
  blind,
  datasetId,
  corrections,
}) {
  const fix =
    corrections?.on && corrections.column_id
      ? {
          corrections: {
            column_id: corrections.column_id,
            column_name: corrections.column_name ?? null,
          },
        }
      : {};
  return mode === "pairwise"
    ? {
        kind: REVIEW_KIND,
        mode,
        run_a: runA,
        run_b: runB,
        blind: Boolean(blind),
        dataset_id: datasetId,
        ...fix,
      }
    : {
        kind: REVIEW_KIND,
        mode,
        run_id: runId,
        blind: false,
        dataset_id: datasetId,
        ...fix,
      };
}

/** A dataset's expected-answer columns (`role: "expected"`), in order. */
export const expectedColumns = (columns) =>
  (columns ?? []).filter((c) => c?.role === "expected" && !c?.deleted);

// -------------------------------------------------------------- pairwise

/**
 * A row's preference as the Review tab reads it, whatever shape R3 sent:
 * `{choice: "A"|"B"|"tie"|null, votes: {A, B, tie}, judge: {choice, model,
 * reason} | null}` against the given pair (A = runA).
 */
export function normalizePreference(pref, runA, runB) {
  if (!pref)
    return { choice: null, votes: { A: 0, B: 0, tie: 0 }, judge: null };
  const fromRun = (v) => {
    if (v == null) return null;
    if (v === "A" || v === "B") return v;
    if (v === "tie" || v === "Tie") return "tie";
    if (v === runA) return "A";
    if (v === runB) return "B";
    return null;
  };
  const votes = pref.votes ?? {};
  const choice =
    pref.source === "judge" && !pref.human_choice
      ? null
      : fromRun(pref.choice ?? pref.human_choice ?? pref.winner);
  const judgeChoice = fromRun(pref.judge?.choice ?? pref.judge?.winner);
  return {
    choice,
    votes: {
      A: votes.A ?? votes[runA] ?? 0,
      B: votes.B ?? votes[runB] ?? 0,
      tie: votes.tie ?? votes.Tie ?? 0,
    },
    judge: pref.judge
      ? {
          choice: judgeChoice,
          model: pref.judge.model ?? null,
          reason: pref.judge.reason ?? "",
        }
      : null,
  };
}

/** Choice -> label value for the pairwise label (`{selected: ["A"|"Tie"|"B"]}`). */
export const choiceValue = (choice) =>
  choice ? { selected: [choice === "tie" ? "Tie" : choice] } : null;

/** Label value -> "A" | "B" | "tie" | null. */
export const valueChoice = (value) => {
  const raw = value?.selected?.[0];
  if (raw === "A" || raw === "B") return raw;
  if (raw === "Tie" || raw === "tie") return "tie";
  return null;
};

/**
 * The three pairwise buttons for a row. `left` = the side ("A" | "B")
 * shown on the left. Keys 1/2/3 = left / tie / right.
 */
export function pairwiseOptions({ left = "A", blind, labelA, labelB }) {
  const right = left === "A" ? "B" : "A";
  const name = (side) => (side === "A" ? labelA : labelB);
  return [
    {
      key: "1",
      choice: left,
      label: blind ? "Left is better" : `${name(left)} is better`,
    },
    { key: "2", choice: "tie", label: "Tie" },
    {
      key: "3",
      choice: right,
      label: blind ? "Right is better" : `${name(right)} is better`,
    },
  ];
}

/**
 * Buttons for a review item in the annotate workspace, from its
 * `review_context`: `Response 1 is better` / `Tie` / `Response 2 is better`
 * when blind, else `#3A is better` / `Tie` / `#4A is better`, ordered by
 * the item's left side.
 */
export function reviewContextButtons(reviewContext) {
  const left = reviewContext?.left === "B" ? "B" : "A";
  const right = left === "A" ? "B" : "A";
  const blind = reviewContext?.blind !== false;
  const nameOf = (side, n) => {
    if (blind) return `Response ${n}`;
    const c = (reviewContext?.candidates ?? []).find((x) => x.side === side);
    return c?.run_label ?? side;
  };
  return [
    { key: "1", choice: left, label: `${nameOf(left, 1)} is better` },
    { key: "2", choice: "tie", label: "Tie" },
    { key: "3", choice: right, label: `${nameOf(right, 2)} is better` },
  ];
}

/** Choice -> how it reads: `#4A is better` / `Tie` / `Response 2 is better`. */
export function choiceText(choice, { labelA, labelB }) {
  if (choice === "tie") return "Tie";
  if (choice === "A") return `${labelA} is better`;
  if (choice === "B") return `${labelB} is better`;
  return "";
}

const pct = (x) => `${Math.round(x * 100)}%`;

/**
 * The win-rate line of a pairwise summary (C7):
 * `{headline: "#4A preferred on 68% of 48 rows", ci: "95% CI 54–79%",
 *   counts: "W29 T7 L12", lowSample}`; null when nothing is judged.
 */
export function winRateLine(summary, { labelB, rows } = {}) {
  if (!summary?.judged) return null;
  const n = rows ?? summary.judged;
  const [lo, hi] = summary.ci ?? [];
  return {
    headline: `${labelB} preferred on ${pct(summary.win_rate)} of ${n} rows`,
    ci:
      lo != null && hi != null
        ? `95% CI ${Math.round(lo * 100)}–${Math.round(hi * 100)}%`
        : null,
    counts: `W${summary.wins} T${summary.ties} L${summary.losses}`,
    lowSample: Boolean(summary.low_sample),
  };
}

/**
 * integration (RV-09): the honest headline of a pairwise summary — the number is
 * a win rate where a tie counts half (results-math winRate), so it says so:
 * `#4A win rate 68% (ties count half) · 48 rows`; null when nothing is
 * judged.
 */
export function winRateHeadline(summary, { labelB, rows } = {}) {
  if (!summary?.judged || summary.win_rate == null) return null;
  const n = rows ?? summary.judged;
  return `${labelB} win rate ${pct(summary.win_rate)} (ties count half) · ${n} ${n === 1 ? "row" : "rows"}`;
}

/**
 * integration (RV-09): the judge-agreement line from ONE count, so it never
 * contradicts itself: `LLM judge agrees with reviewers on 33 of 41 rows
 * (80%) · differs on 8`; null with no row both judged.
 */
export function judgeAgreementLine(agreement) {
  if (!agreement?.n) return null;
  const differ = Math.max(0, agreement.n - (agreement.agree ?? 0));
  return `LLM judge agrees with reviewers on ${agreement.agree} of ${agreement.n} ${agreement.n === 1 ? "row" : "rows"} (${pct(agreement.agree / agreement.n)})${differ ? ` · differs on ${differ}` : ""}`;
}

/**
 * integration (RV-09): your own picks against the judge's in this queue (the
 * Reveal table), labelled as yours: `Your picks differ from the judge on 1
 * of 6 rows`; null when no row has both.
 */
export function myJudgeDiffLine(reveal) {
  const both = (reveal ?? []).filter(
    (r) => r.agree === true || r.agree === false,
  );
  if (!both.length) return null;
  const differ = both.filter((r) => r.agree === false).length;
  return differ
    ? `Your picks differ from the judge on ${differ} of ${both.length} ${both.length === 1 ? "row" : "rows"}`
    : `Your picks match the judge on all ${both.length} ${both.length === 1 ? "row" : "rows"}`;
}

/** Compare page for a pair: `DS/compare/data?runs=a,b&baseline=a`. */
export const compareUrl = (datasetId, runA, runB) =>
  `/dashboard/develop/${datasetId}/compare/data?runs=${runA},${runB}&baseline=${runA}`;

/** The review page for a queue, returning to `from`. */
export const reviewPageUrl = (datasetId, queueId, { from, itemId } = {}) => {
  const qs = new URLSearchParams();
  if (itemId) qs.set("itemId", itemId);
  if (from) qs.set("from", from);
  const s = qs.toString();
  return `/dashboard/develop/${datasetId}/review/${queueId}${s ? `?${s}` : ""}`;
};

/** Exit label of the review page for a return URL. */
export function exitLabelFor(from) {
  if (!from) return "Back to experiments";
  if (from.includes("/compare/")) return "Back to compare runs";
  if (from.includes("/experiment/")) return "Back to experiment";
  return "Back to experiments";
}

// ------------------------------------------------------- start here (R3)

/** The pairwise label every pairwise queue uses (created when missing). */
export const PAIRWISE_LABEL = Object.freeze({
  name: "Overall preference",
  type: "categorical",
  description: "Which response is better for the customer?",
  settings: {
    kind: "pairwise",
    options: [{ label: "A" }, { label: "Tie" }, { label: "B" }],
    multi_choice: false,
    higher_is_better: true,
  },
});

/** Seconds a reviewer spends on one blind pairwise row (agent replies). */
export const SECONDS_PER_PAIR = 40;

/** Minutes to review `n` rows: 1–5 exact, then to the nearest 5. */
export function reviewMinutes(n, secondsPerRow = SECONDS_PER_PAIR) {
  const count = Math.max(0, Number(n) || 0);
  if (!count) return 0;
  const min = Math.max(1, Math.ceil((count * secondsPerRow) / 60));
  return min <= 5 ? min : Math.round(min / 5) * 5;
}

/** `Review the 14 regressions (about 10 min)` · `Review the 1 regression (about 1 min)`. */
export function regressionReviewLabel(n) {
  const count = Math.max(0, Number(n) || 0);
  return `Review the ${count} ${count === 1 ? "regression" : "regressions"} (about ${reviewMinutes(count)} min)`;
}

/** `Regressions · #2D vs #2B · Oct 16`. */
export function regressionQueueName({ labelA, labelB }, date) {
  return `Regressions · ${labelB} vs ${labelA} · ${shortDate(date ?? new Date())}`;
}

/**
 * An open regressions queue for this pair with work left (reuse it instead of
 * creating a second one), else null.
 */
export function openRegressionQueue(queues, { runA, runB }) {
  return (
    (queues ?? []).find(
      (q) =>
        q?.metadata?.focus === "regressions" &&
        q.metadata.run_a === runA &&
        q.metadata.run_b === runB &&
        (q.item_count ?? 0) > (q.completed_count ?? 0),
    ) ?? null
  );
}

/**
 * What a submitted pairwise vote reads as on the toast (`Response 1 is
 * better`, `Tie`), blind-safe: the item's own sides. Null when the
 * annotations hold no pairwise value.
 */
export function savedChoiceText(annotations, reviewContext) {
  for (const a of annotations ?? []) {
    const sel = a?.value?.selected?.[0];
    if (!sel) continue;
    const choice = String(sel).toLowerCase() === "tie" ? "tie" : sel;
    const button = reviewContextButtons(reviewContext).find(
      (b) => b.choice === choice,
    );
    if (button) return button.label;
  }
  return null;
}
