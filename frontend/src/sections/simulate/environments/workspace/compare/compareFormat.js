import { runColor } from "../runs/runs.constants";

// What a missing value reads as on the compare page.
export const NONE = "-";

export const plural = (n, word, many = `${word}s`) =>
  `${n} ${n === 1 ? word : many}`;

// A run's agent version as a label. The executions API sends a string today;
// an object (`{ name }`) is read by its name.
export const agentVersionLabel = (version) => {
  if (!version) return null;
  if (typeof version === "string") return version;
  return version.name || version.version_name || version.label || null;
};

export const agentText = (run) =>
  `agent ${agentVersionLabel(run?.agentVersion) ?? NONE}`;

export const passRateOf = (run) =>
  run?.total ? Math.round((run.passed / run.total) * 100) : null;

// "75% 9/12": the share of the run's calls that passed, with its fraction.
export const passText = (run) => {
  const rate = passRateOf(run);
  return rate == null ? NONE : `${rate}% ${run.passed}/${run.total}`;
};

// A run as the compare page draws it: its ordinal is its letter, and the same
// ordinal-keyed colour the runs list and the run detail use.
export const withIdentity = (run) => ({
  ...run,
  letter: run?.ordinal != null ? String(run.ordinal) : "?",
  color: runColor(run?.ordinal),
});

// Which scenarios the runs covered, from each run's own scenario keys. Null
// when a run doesn't list its keys, since a count would then be a guess.
export const coverageFromRuns = (runs) => {
  if (!runs.length || runs.some((r) => !r.scenarioIds?.length)) return null;
  const sets = runs.map((r) => new Set(r.scenarioIds));
  const union = new Set(runs.flatMap((r) => r.scenarioIds));
  const shared = [...union].filter((id) => sets.every((s) => s.has(id)));
  return {
    total: union.size,
    shared: shared.length,
    partial: union.size - shared.length,
    unmeasured: 0,
  };
};

// The repeats each run used, smallest first.
export const repeatsOf = (runs) =>
  [...new Set(runs.map((r) => r.trials || 1))].sort((a, b) => a - b);

// "2 runs · 2 agent versions · 30 scenarios in every run · 3 repeats each".
// Runs sampled a different number of times aren't the same measurement, so
// the subtitle says so rather than setting their rates side by side as equals.
export const subtitleText = (runs, coverage) => {
  const versions = new Set(
    runs.map((r) => agentVersionLabel(r.agentVersion) ?? NONE),
  );
  const parts = [
    plural(runs.length, "run"),
    plural(versions.size, "agent version"),
  ];
  if (coverage) {
    parts.push(
      `${coverage.shared} ${coverage.shared === 1 ? "scenario" : "scenarios"} in every run`,
    );
    if (coverage.partial > 0) parts.push(`${coverage.partial} in only some`);
    if (coverage.unmeasured > 0)
      parts.push(`${coverage.unmeasured} not measured`);
  }
  const repeats = repeatsOf(runs);
  parts.push(
    repeats.length === 1
      ? `${plural(repeats[0], "repeat")} each`
      : `repeats per scenario differ (${repeats.join(", ")})`,
  );
  return parts.join(" · ");
};
