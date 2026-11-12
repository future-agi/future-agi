// Run metrics as an Explore measure (W5-4 B2). Pure. `rm:<metric id>` names
// a dataset's run metric in the rail's Measure (Pivot & charts) and on the
// chart board; its numbers come only from GET …/derived-metrics/?runs=.

export const RUN_METRIC_PREFIX = "rm:";
export const isRunMetricKey = (key) =>
  String(key ?? "").startsWith(RUN_METRIC_PREFIX);

/** Run metric options for the rail's Measure (`Run metrics ▸ <name>`). */
export const runMetricMeasures = (metrics) =>
  (metrics ?? []).map((m) => ({
    key: `${RUN_METRIC_PREFIX}${m.id}`,
    label: `Run metrics ▸ ${m.name}`,
    short: m.name,
    kind: "run_metric",
  }));
