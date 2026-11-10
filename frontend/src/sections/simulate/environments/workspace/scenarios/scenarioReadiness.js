import {
  useReadiness,
  useReadinessCases,
} from "src/api/simulate-environments/readiness";

// Worst first: a scenario in two lists takes the first.
export const BUCKETS = ["blocked", "stale", "untested"];

/**
 * Scenario key → "blocked" | "stale" | "untested" from a whole-suite
 * readiness report. A list the report cut at 200 is read from `paged`
 * (bucket → every key) instead.
 */
export function scenarioReadiness(report, paged = {}) {
  const lists = report?.selection?.lists || {};
  const marks = new Map();
  BUCKETS.forEach((bucket) => {
    const list = lists[bucket];
    const keys = list?.truncated && paged[bucket] ? paged[bucket] : list?.keys;
    (keys || []).forEach((key) => {
      if (!marks.has(key)) marks.set(key, bucket);
    });
  });
  return marks;
}

// Readiness of every scenario for the environment's default versions.
export function useScenarioReadiness(env) {
  const enabled = Boolean(env?.versioning?.enabled);
  const { data } = useReadiness(env?.id, {}, { enabled });
  const lists = data?.selection?.lists || {};
  const blocked = useReadinessCases(env?.id, {
    bucket: "blocked",
    enabled: enabled && Boolean(lists.blocked?.truncated),
  });
  const stale = useReadinessCases(env?.id, {
    bucket: "stale",
    enabled: enabled && Boolean(lists.stale?.truncated),
  });
  const untested = useReadinessCases(env?.id, {
    bucket: "untested",
    enabled: enabled && Boolean(lists.untested?.truncated),
  });
  return scenarioReadiness(data, {
    blocked: blocked.data,
    stale: stale.data,
    untested: untested.data,
  });
}
