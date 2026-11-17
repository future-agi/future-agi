// The Analytics ranges and the bucket size each one asks for. The analytics
// API truncates by minute, hour, day, week or month
// (agentcc/services/analytics.py TRUNC_MAP) and answers 400 for an unknown
// granularity or for a window over 2,000 buckets, so every range asks for
// one of these sizes, chosen so a range is a few hundred points at most.

export const ANALYTICS_GRANULARITIES = Object.freeze(["minute", "hour", "day"]);

export const ANALYTICS_RANGES = Object.freeze([
  {
    value: "1h",
    label: "1H",
    period: "hour",
    ms: 60 * 60 * 1000,
    granularity: "minute",
  },
  {
    value: "6h",
    label: "6H",
    period: "6 hours",
    ms: 6 * 60 * 60 * 1000,
    granularity: "minute",
  },
  {
    value: "24h",
    label: "24H",
    period: "24 hours",
    ms: 24 * 60 * 60 * 1000,
    granularity: "hour",
  },
  {
    value: "7d",
    label: "7D",
    period: "7 days",
    ms: 7 * 24 * 60 * 60 * 1000,
    granularity: "hour",
  },
  {
    value: "30d",
    label: "30D",
    period: "30 days",
    ms: 30 * 24 * 60 * 60 * 1000,
    granularity: "day",
  },
  {
    value: "90d",
    label: "90D",
    period: "90 days",
    ms: 90 * 24 * 60 * 60 * 1000,
    granularity: "day",
  },
]);

export const DEFAULT_RANGE = "24h";

/** { start, end, granularity } for a range preset, ending now. */
export function rangeWindow(preset, now = new Date()) {
  const range =
    ANALYTICS_RANGES.find((r) => r.value === preset) ||
    ANALYTICS_RANGES.find((r) => r.value === DEFAULT_RANGE);
  return {
    start: new Date(now.getTime() - range.ms).toISOString(),
    end: now.toISOString(),
    granularity: range.granularity,
  };
}
