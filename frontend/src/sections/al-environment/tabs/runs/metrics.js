/**
 * Which of ALK's metrics actually measured a run.
 *
 * Three groups, because they are three different claims. Most metrics score 1.00 because there
 * was nothing to measure — "No required browser trace keys provided" — and a green bar reading
 * "browser action safety 1.00" on a phone call is a lie the page would be telling on ALK's
 * behalf. ALK marks those itself with `applicable: false`; this only stops hiding it.
 */
export const splitMetrics = (metrics) => {
  const all = Array.isArray(metrics)
    ? metrics
    : Object.entries(metrics || {}).map(([name, score]) => ({
        name,
        score,
        applicable: true,
        reason: "",
      }));
  const applicable = all.filter((one) => one.applicable !== false);
  return {
    // Anything that moved off 1.00 is a real result and leads, weakest first.
    measured: applicable
      .filter((one) => Number(one.score) < 1)
      .sort((a, b) => Number(a.score) - Number(b.score)),
    // Ran, found nothing wrong. Worth counting, not worth a bar each.
    clean: applicable.filter((one) => Number(one.score) >= 1),
    // Nothing to measure. Never a score.
    absent: all.filter((one) => one.applicable === false),
  };
};

/** Metric names are written for code; a bar is read by a person. */
export const metricLabel = (name) => String(name).replace(/_/g, " ");
