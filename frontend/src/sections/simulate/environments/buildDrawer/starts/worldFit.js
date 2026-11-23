// The verdict of a fit: the whole suite measured, part of it, or none of it (no tool paired).
export function fitVerdict(fit) {
  const none = !fit.rows.some((row) => row.agent);
  return {
    none,
    verdict: none ? "none" : fit.unmeasured.length ? "partial" : "ready",
  };
}
