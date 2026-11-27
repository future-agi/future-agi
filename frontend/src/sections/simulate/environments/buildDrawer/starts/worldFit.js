// The verdict of a fit: the whole suite measured, part of it, or none of it.
export function fitVerdict(fit) {
  const measurable = fit.total - fit.unmeasured.length;
  const none = measurable <= 0;
  return {
    measurable,
    none,
    verdict: none ? "none" : fit.unmeasured.length ? "partial" : "ready",
  };
}

/** The fit's rows with the pairing the person changed (null: as suggested) laid over them. */
export function pairedRows(fit, pairing) {
  if (!pairing) return fit.rows;
  return fit.rows.map((row) =>
    row.world in pairing ? { ...row, agent: pairing[row.world] } : row,
  );
}

/** What the table shows as the pairing to send: each world tool and the agent's tool, or null. */
export const pairingOf = (rows) =>
  Object.fromEntries(rows.map((row) => [row.world, row.agent]));
