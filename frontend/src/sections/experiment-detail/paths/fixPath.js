// Paths · `Fix this path` (iterate r1 G3 P1): which run a failing path row
// fixes, the node Test a fix opens on, and the reason line at the top of
// the dock. Pure; every number comes from the PathAggregate row.
import { pathText } from "src/sections/common/Trajectory/pathModel";

/**
 * The run side a path row fixes: the side whose tries on it fail (this run
 * first), or null when no try on it fails.
 * @param {object} row  aggregate path (single run) or comparePaths row
 * @param {{compare: boolean, prun: string, pcmp?: string|null}} opts
 * @returns {{runId: string, path: object}|null}
 */
export function fixTargetOf(row, { compare, prun, pcmp }) {
  if (!row) return null;
  if (!compare)
    return row.failed > 0 && prun ? { runId: prun, path: row } : null;
  if (row.a?.failed > 0 && prun) return { runId: prun, path: row.a };
  if (row.b?.failed > 0 && pcmp) return { runId: pcmp, path: row.b };
  return null;
}

/**
 * The finding (R5 root cause) of `runId` that names rows on this path, or
 * null.
 */
export function causeForPath(causes, runId, path) {
  const rows = new Set(path?.example_rows ?? []);
  if (!rows.size) return null;
  return (
    (causes ?? []).find(
      (c) =>
        c?.run_id === runId &&
        (c.rows ?? []).some((r) => rows.has(r.row_id ?? r)),
    ) ?? null
  );
}

/**
 * The agent node a path's failure is fixed on: the finding's node when it is
 * an LLM node of the run; else the nearest LLM node before the first step of
 * the path that is not an LLM node (a tool step belongs to the node that
 * called it); else the run's first LLM node (the one that reads the task).
 * @param {{steps?: {name: string}[]}} path
 * @param {{id: string}[]} nodes  llmNodes(config)
 * @param {object|null} cause
 * @returns {string|null}
 */
export function ownerNodeOf(path, nodes, cause) {
  const ids = (nodes ?? []).map((n) => n.id);
  if (!ids.length) return null;
  if (cause?.node && ids.includes(cause.node)) return cause.node;
  const steps = path?.steps ?? [];
  let owner = null;
  for (const s of steps) {
    if (ids.includes(s.name)) owner = s.name;
    else return owner ?? ids[0];
  }
  return owner ?? ids[0];
}

/**
 * `Fixing: lookup_order ×2 → search_kb → reply · fails 18 of 18 tries ·
 * first wrong step: lookup_order (order id without "A-")`. The first wrong
 * step is printed only when a finding names it.
 */
export function fixReasonOf(path, cause) {
  if (!path) return null;
  const tries = path.attempts === 1 ? "try" : "tries";
  const parts = [
    `Fixing: ${pathText(path.steps)}`,
    `fails ${path.failed} of ${path.attempts} ${tries}`,
  ];
  const fd = cause?.first_divergence;
  const step = fd?.step_name ?? null;
  if (step) {
    // The finding's title says more than a generic `<step> output changed`.
    const generic = !fd.detail || / output changed$/.test(fd.detail);
    const why = (generic ? cause.title : fd.detail) ?? fd.detail ?? null;
    parts.push(`first wrong step: ${step}${why ? ` (${why})` : ""}`);
  }
  return parts.join(" · ");
}
