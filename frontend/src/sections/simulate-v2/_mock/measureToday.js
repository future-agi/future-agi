/*
  Measure today (RSI PRD, "How it works": "run the current agent on the
  scenarios. This sets the baseline"; Environments: "Each improvement still
  runs its own baseline next to its candidates").

  An improvement's first step runs the environment's scenarios on the
  version in production, so its baseline and its must-pass set ("passes all
  its repeats on the current version") are always today's — never read off
  an old run on another version, and nobody has to run the environment by
  hand first. It's a real run of the environment: recorded in its Runs,
  named for what started it (`measuredFor`), and linked back to the
  improvement from there.
*/
import { settledRun } from "./runGroup";
import { runSummaries } from "./comparison";
import { getEnvironment } from "./environments";
import { protoRunId } from "./executionAdapter";
import {
  currentAgentVersion,
  currentEnvVersion,
  productionAgentVersion,
} from "./versions";
import { repeatsFor } from "./improvementStart";
import { pinsForNewRun } from "./envModel";

/**
 * The run measuring today, as it will be recorded. Deterministic for a
 * `key`, so the estimate shown before Start and the run Start records match.
 *   measuredFor — { kind: "fix", issueId } or { kind: "goal" }
 */
export const measureTodayRun = (
  env,
  es,
  { key, measuredFor, startedBy, now = new Date() },
) => {
  if (!env || !key) return null;
  const resolved = getEnvironment(env.id) || env;
  const version =
    productionAgentVersion(es)?.label || currentAgentVersion(es)?.label || null;
  const id = protoRunId(env.id, `${key}-today`.toLowerCase());
  const repeats = repeatsFor(resolved, es);
  return settledRun(
    resolved,
    es,
    {
      /* The run rules the run page stamps when it plays — set here too, so
       the plan read before the run and the results read after it are the
       same run (its must-pass set and its failures never drift apart). */
      ...pinsForNewRun(resolved, es, {
        trials: repeats,
        seed: id,
        rules: true,
      }),
      id,
      ordinal: Math.max(0, ...(es?.runs || []).map((r) => r.ordinal || 0)) + 1,
      status: "running",
      startedAt: now.toISOString(),
      startedBy,
      agentVersion: version,
      envVersion: currentEnvVersion(resolved, es)?.label,
      /* The same repeats every run of the improvement uses (two for voice). */
      repeats,
      partial: false,
      seed: id,
      measuredFor,
    },
    now,
  );
};

/** A run as every reader of runs sees it — with its tasks. */
export const measuredSummaryOf = (env, es, run) =>
  run
    ? runSummaries(getEnvironment(env.id) || env, { ...es, runs: [run] })[0] ||
      null
    : null;
