/*
  What one run's cell says on the compare table, as plain data — shared by
  the one-row-per-scenario table and its expanded per-run lines.
*/
import { hashSeed, turnsOf } from "./runStream";
import { trialTaskOf } from "./callRecord";
import { csatOf, formatLatency, latencyOf } from "../run/traceTableHelpers";

/* The cause in a few words; the full sentence is on hover.
   "Task success failed — Met the configured…" → "Task success failed". */
export const shortCause = (deciding) => {
  if (!deciding) return "";
  if (deciding.kind === "claim") return "Said, not done";
  return String(deciding.text || "")
    .split(" — ")[0]
    .split(", which")[0]
    .replace(/\.$/, "");
};

/* What a run's cell says: a pass or flaky in one word, a failure as its
   cause (red), not scored in grey; a near miss adds a short note. */
export const phraseOf = (c) => {
  if (!c) return { text: "Not run", color: "text.disabled" };
  if (c.status === "passed")
    return {
      text: "Passed",
      color: "status.success.fg",
      note:
        c.deciding?.kind === "near"
          ? shortCause(c.deciding).replace(/^Passed, but /, "")
          : null,
    };
  if (c.status === "flaky")
    return { text: "Flaky", color: "status.warning.fg" };
  if (c.status === "failed")
    return { text: shortCause(c.deciding), color: "status.danger.fg" };
  if (c.status === "missing")
    return { text: "Not run", color: "text.disabled" };
  return { text: "Not scored", color: "text.disabled" };
};

/* "3/3" — trials passed of trials run, when there were trials to count. */
/* Only on flaky: "Passed" already means every trial and "Failed" none, so
   a count there just repeats itself on every row. */
export const trialCountOf = (c) =>
  c && c.status === "flaky" && c.repeats > 1 && c.passes != null && c.measured
    ? `${c.passes}/${c.repeats}`
    : null;

/* The system-metric columns: value(cell) is the number, text(v) how it
   reads, delta(d) how a change reads; w is the column width. */
export const metricDefsOf = (view) =>
  [
    view.columns.csat && {
      key: "csat",
      label: "CSAT",
      w: 52,
      value: (c) => (c?.task ? csatOf(c.task) : null),
      text: (v) => String(v),
      delta: (d) => `${Math.round(Math.abs(d) * 10) / 10}`,
    },
    view.columns.turns && {
      key: "turns",
      label: "Turns",
      w: 52,
      value: (c) => (c?.task ? turnsOf(c.task.steps || []) : null),
      text: (v) => String(v),
      delta: (d) => String(Math.abs(d)),
      lowerIsBetter: true,
    },
    view.columns.latency && {
      key: "latency",
      label: "Latency",
      w: 100,
      value: (c) => (c?.task ? latencyOf(c.task) : null),
      text: (v) => formatLatency(v),
      delta: (d) => formatLatency(Math.abs(d)),
      lowerIsBetter: true,
    },
    view.columns.duration && {
      key: "duration",
      label: "Duration",
      w: 88,
      value: (c) => (c?.task ? c.durationMs : null),
      text: (v) => `${(v / 1000).toFixed(1)}s`,
      delta: (d) => `${(Math.abs(d) / 1000).toFixed(1)}s`,
      lowerIsBetter: true,
    },
    view.columns.tokens && {
      key: "tokens",
      label: "Tokens",
      w: 104,
      value: (c) => (c?.task ? c.tokens : null),
      text: (v) => v.toLocaleString(),
      delta: (d) => Math.abs(d).toLocaleString(),
      lowerIsBetter: true,
    },
    view.columns.cost && {
      key: "cost",
      label: "Model cost",
      w: 96,
      value: (c) => (!c?.task || c.costUnverified ? null : c.cost || 0),
      text: (v) => `≈ $${v.toFixed(2)}`,
      delta: (d) => `$${Math.abs(d).toFixed(2)}`,
      lowerIsBetter: true,
    },
  ].filter(Boolean);

/* An eval's score on a run's cell, 0–100, or null when it wasn't scored. */
export const evalPctOf = (c, evalId) => {
  const r = (c?.task?.evalResults || []).find((x) => x.id === evalId);
  return r ? { pct: Math.round(r.score * 100), passed: !!r.passed } : null;
};

/* Did this run's cell do better or worse than the baseline's, by the
   verdict that counts (passed every trial)? Passed before and not now is
   regressed; not passing before and passing every trial now is improved. */
export const movementOf = (cell, baseCell) => {
  if (!cell || !baseCell || cell === baseCell) return null;
  const counted = (c) => ["passed", "flaky", "failed"].includes(c.status);
  if (!counted(cell) || !counted(baseCell)) return null;
  const was = baseCell.status === "passed";
  const now = cell.status === "passed";
  if (was && !now) return "regressed";
  if (!was && now) return "improved";
  return null;
};

/* One trial of a run's cell, shaped like a cell so the same columns read it:
   its own verdict, reason, length and numbers (trial 1 is the run's call). */
export const trialCellOf = (cell, k) => {
  if (!cell?.task) return null;
  const scoredCell = ["passed", "flaky", "failed"].includes(cell.status);
  if (!scoredCell) return { ...cell, trialIndex: k };
  const t = trialTaskOf(cell.task, k);
  const h = hashSeed(`${cell.task.id}:${cell.runId}:trial:${k}`) >>> 0;
  const nudge = k === 0 ? 1 : 0.9 + (h % 21) / 100;
  const failed = (t.evalResults || []).find((e) => !e.passed);
  const claim = t.status === "failed" && t.callLog?.unsupportedClaim;
  /* Each trial is its own call: its scores move a little even when its
     verdict matches trial 1's. */
  const evalResults =
    k === 0
      ? t.evalResults
      : (t.evalResults || []).map((e, i) => ({
          ...e,
          score: Math.max(
            0,
            Math.min(1, (e.score ?? 0) + (((h >>> (i * 3)) % 7) - 3) / 100),
          ),
        }));
  return {
    ...cell,
    trialIndex: k,
    task: { ...t, evalResults },
    status: t.status,
    passes: null,
    repeats: null,
    durationMs: t.durationMs,
    tokens: Math.round((cell.tokens || 0) * nudge),
    deciding:
      t.status === "passed"
        ? { kind: "clean", text: "" }
        : claim
          ? { kind: "claim", text: claim }
          : failed
            ? {
                kind: "eval",
                text: `${failed.name} failed — ${failed.reason || ""}`.trim(),
              }
            : { kind: "unclear", text: "Failed" },
  };
};

/* How many trials a scenario ran, across the compared runs. */
export const trialCountAcross = (cells) =>
  Math.max(0, ...cells.map((c) => c?.task?.samples?.length || 0));
