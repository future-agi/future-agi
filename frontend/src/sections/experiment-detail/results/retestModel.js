// A re-test of production failures (iterate r2 G3 P1, seam S2). Pure.
// Rows imported from production carry `production: {outcome, criterion,
// reason, failing_turn, reply}` (G5); the new run's results carry the same
// criterion's scores. The page answers "does it still fail?":
//   `Re-test of 6 production failures: 4 now pass · 2 still fail (Resolution)`
import { conversationOf as readConversation } from "src/sections/common/Media/mediaRef";
import { primaryPassMetric, trialOutcome } from "./resultsSimple";

const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);

const CUSTOMER = new Set(["customer", "user", "human", "caller"]);

/**
 * The conversation among a row's inputs: `[{role, text}]` (the shared
 * Media conversation reader), or null.
 */
export function conversationOf(inputs) {
  if (!isObj(inputs)) return null;
  for (const raw of Object.values(inputs)) {
    const c = readConversation(raw);
    if (c?.turns?.length)
      return c.turns.map((t) => ({
        role: CUSTOMER.has(t.role) ? "customer" : t.role,
        text: t.text ?? "",
      }));
  }
  return null;
}

/** The row's production record when it failed there, else null. */
export const productionOf = (row) => {
  const p = row?.production ?? row?.metadata?.production ?? null;
  return isObj(p) ? p : null;
};

/**
 * The failing turn of a multi-turn row: `{total, n, index, customer}` —
 * `n` = production's `failing_turn` (1-based, a message of the
 * conversation); `customer` = the customer message that turn answers (the
 * one at `n` when it is the customer's, else the last before it).
 */
export function failingTurnOf(row) {
  const p = productionOf(row);
  const turns = conversationOf(row?.inputs);
  if (!turns?.length || !Number.isFinite(p?.failing_turn)) return null;
  const n = Math.max(1, Math.min(turns.length, Math.round(p.failing_turn)));
  let index = n - 1;
  while (index > 0 && turns[index].role !== "customer") index -= 1;
  return {
    total: turns.length,
    n,
    index,
    turns,
    customer: turns[index]?.role === "customer" ? turns[index].text : null,
  };
}

/** The metric that scores the production criterion (`Resolution`). */
export function criterionMetric(metrics, criterion) {
  const list = metrics ?? [];
  const want = String(criterion ?? "").toLowerCase();
  return (
    (want &&
      list.find(
        (m) =>
          String(m.name ?? "").toLowerCase() === want ||
          String(m.key ?? "")
            .toLowerCase()
            .replace(/^(eval|check):/, "") === want,
      )) ||
    primaryPassMetric(list)
  );
}

/** `✗ Failed · Resolution · turn 5`. */
export function productionCellText(p) {
  if (!p) return "";
  const failed = String(p.outcome ?? "").toLowerCase() === "fail";
  return [
    failed ? "✗ Failed" : "✓ Passed",
    p.criterion ?? null,
    Number.isFinite(p.failing_turn) ? `turn ${p.failing_turn}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * @param {object[]} rows  results rows
 * @param {{runId: string, metrics: object[]}} opts  the re-test run
 * @returns {{total, nowPass, stillFail, notScored, stillIds: string[],
 *   criterion: string, metricKey: string|null, text: string}|null}
 */
export function retestSummary(rows, { runId, metrics }) {
  const failed = (rows ?? []).filter(
    (r) => String(productionOf(r)?.outcome ?? "").toLowerCase() === "fail",
  );
  if (!failed.length || !runId) return null;
  const criterion =
    failed.map((r) => productionOf(r).criterion).find(Boolean) ?? "the check";
  const metric = criterionMetric(metrics, criterion);
  let nowPass = 0;
  let notScored = 0;
  const stillIds = [];
  failed.forEach((r) => {
    const o = trialOutcome(r.results?.[runId], metric?.key);
    if (o.tone === "pass") nowPass += 1;
    else if (o.tone === "none") notScored += 1;
    else stillIds.push(r.example_id);
  });
  const total = failed.length;
  const parts = [`${nowPass} now pass`, `${stillIds.length} still fail`];
  if (notScored) parts.push(`${notScored} not scored yet`);
  return {
    total,
    nowPass,
    stillFail: stillIds.length,
    notScored,
    stillIds,
    criterion,
    metricKey: metric?.key ?? null,
    text: `Re-test of ${total} production ${total === 1 ? "failure" : "failures"}: ${parts.join(" · ")} (${criterion})`,
  };
}
