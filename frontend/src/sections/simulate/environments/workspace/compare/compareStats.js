import { flipOf } from "./useComparison";
import {
  NO_CHANGE_POINTS,
  pairedInterval,
  verdictLabelOf,
} from "./pairedInterval";

/**
 * The scenarios two runs both measured, as pass shares with the trials each
 * rests on: the input `pairedInterval` reads.
 */
export function pairsBetween(rows, baseId, runId) {
  return rows
    .map((row) => {
      const a = row.verdicts[baseId];
      const b = row.verdicts[runId];
      if (a?.share == null || b?.share == null) return null;
      return {
        id: row.id,
        a: a.share,
        b: b.share,
        ka: a.measured,
        kb: b.measured,
      };
    })
    .filter(Boolean);
}

/**
 * One run read against the baseline: how many scenarios paired, which way
 * they moved, what sat outside the pairs, and the interval over the pairs.
 */
export function readPair(rows, baseId, runId) {
  const counts = { paired: 0, new: 0, removed: 0, regressed: 0, improved: 0 };
  rows.forEach((row) => {
    const a = row.verdicts[baseId];
    const b = row.verdicts[runId];
    const inA = a?.status !== "missing";
    const inB = b?.status !== "missing";
    if (inA && inB) {
      counts.paired += 1;
      const { kind } = flipOf(a, b);
      if (kind === "regressed") counts.regressed += 1;
      if (kind === "improved") counts.improved += 1;
    } else if (inB) counts.new += 1;
    else if (inA) counts.removed += 1;
  });
  const interval = pairedInterval(pairsBetween(rows, baseId, runId));
  const total = counts.paired + counts.new + counts.removed;
  const sentence = [
    `Compared on ${counts.paired} of ${total}`,
    counts.new && `${counts.new} new`,
    counts.removed && `${counts.removed} removed`,
  ]
    .filter(Boolean)
    .join(" · ");
  return {
    baselineId: baseId,
    targetId: runId,
    counts,
    total,
    interval,
    label: verdictLabelOf(interval, NO_CHANGE_POINTS),
    sentence,
  };
}

/**
 * The verdict for the page: the baseline against the newest run, and with
 * three or more runs, each run's pass rate over the scenarios every run
 * measured.
 */
export function readComparison(comparison, runIds) {
  if (runIds.length < 2) return null;
  const read = readPair(comparison.rows, runIds[0], runIds[runIds.length - 1]);
  let trend = null;
  if (runIds.length >= 3) {
    const everywhere = comparison.rows.filter((row) =>
      runIds.every((id) => row.verdicts[id]?.share != null),
    );
    trend = {
      n: everywhere.length,
      points: runIds.map((id) => ({
        runId: id,
        rate: everywhere.length
          ? Math.round(
              (everywhere.reduce((a, row) => a + row.verdicts[id].share, 0) /
                everywhere.length) *
                100,
            )
          : null,
      })),
    };
  }
  return { ...read, trend };
}

/**
 * One eval's scores in ten bands, one count per run: whether a run got better
 * everywhere or stopped failing badly on a few scenarios.
 */
export function distributionFor(rows, runIds, evalId) {
  const bands = Array.from({ length: 10 }, (_, i) => ({
    label: `${i * 10}-${(i + 1) * 10}%`,
    counts: runIds.map(() => 0),
  }));
  rows.forEach((row) =>
    runIds.forEach((runId, ri) =>
      row.cells[runId].forEach((call) => {
        const score = call.evalResults.find((r) => r.id === evalId)?.score;
        if (typeof score !== "number") return;
        bands[Math.min(9, Math.floor(score * 10))].counts[ri] += 1;
      }),
    ),
  );
  return bands;
}

const pct = (v) => (v == null ? "-" : `${Math.round(v)}%`);

/**
 * The comparison with the scenarios taken out: each run's totals, read
 * against the baseline. Every number comes from the runs' calls.
 */
export function summaryLines(comparison, runIds) {
  const { coverage, evals, runStats } = comparison;
  const lines = [
    {
      id: "passRate",
      label: coverage.common
        ? `Pass rate · ${coverage.common} shared scenarios`
        : "Pass rate",
      get: (s) => (coverage.common ? s.sharedPassRate : s.passRate),
      format: pct,
    },
    {
      id: "flaky",
      label: "Flaky scenarios",
      get: (s) => s.flaky,
      format: (v) => `${v}`,
      lowerIsBetter: true,
    },
    {
      id: "duration",
      label: "Avg duration",
      get: (s) => s.avgDurationMs,
      format: (v) => (v == null ? "-" : `${(v / 1000).toFixed(1)}s`),
      lowerIsBetter: true,
    },
    {
      id: "tokens",
      label: "Tokens",
      get: (s) => s.tokens,
      format: (v) => (v == null ? "-" : v.toLocaleString()),
      lowerIsBetter: true,
    },
    {
      id: "csat",
      label: "Avg CSAT",
      get: (s) => s.avgCsat,
      format: (v) => (v == null ? "-" : `${Math.round(v * 10) / 10}`),
    },
    ...evals.map((e) => ({
      id: `eval:${e.id}`,
      label: e.name,
      get: (s) => s.evalPassRates[e.id],
      format: pct,
    })),
  ];
  const baseline = runStats[runIds[0]];
  return lines.map((line) => ({
    ...line,
    values: runIds.map((runId, i) => {
      const now = line.get(runStats[runId]);
      const before = line.get(baseline);
      const moved = i > 0 && now != null && before != null && now !== before;
      return {
        runId,
        text: line.format(now),
        moved,
        better: moved
          ? line.lowerIsBetter
            ? now < before
            : now > before
          : null,
      };
    }),
  }));
}
