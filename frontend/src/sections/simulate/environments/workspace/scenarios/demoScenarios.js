/**
 * POC-only: inflate a small env's scenarios into a large synthetic set so the
 * pagination + select-all-matching behaviour can be exercised against thousands
 * of rows without a backend. Activated by `?scnDemo=<count>` on the workspace
 * URL; absent, the real scenarios pass through untouched.
 *
 * Rows are cloned from the real ones (so personas/sub-goals/use-cases stay
 * realistic and the group-by axes have real variety) with unique ids and an
 * index-stamped name. This file is deleted when the real backend list endpoint
 * lands — it only exists to make the seam demonstrable.
 */
export function inflateScenarios(rows, count) {
  if (!count || !rows?.length || count <= rows.length) return rows;
  const out = [];
  for (let i = 0; i < count; i += 1) {
    const base = rows[i % rows.length];
    out.push({
      ...base,
      id: `demo-${i}-${base.id || "s"}`,
      name: `${base.name || base.title || "scenario"} #${i + 1}`,
    });
  }
  return out;
}

// Read the demo scale off the current URL (0 when absent/invalid).
export function demoScaleFromSearch(search = "") {
  const n = Number(new URLSearchParams(search).get("scnDemo"));
  return Number.isFinite(n) && n > 0 ? Math.min(n, 20000) : 0;
}
