/*
  Where a run was started from: the console, the CLI, Python, CI or the
  developer's own agent over MCP. A run records `via` when it starts; runs
  recorded before that field existed (the seeded history) get a stable one
  from their id, so the list shows the mix a real workspace would.
*/
export const RUN_VIA = {
  console: { label: "Console", icon: "solar:widget-linear" },
  cli: { label: "CLI", icon: "solar:command-linear" },
  sdk: { label: "Python", icon: "solar:code-linear" },
  ci: { label: "CI", icon: "solar:branching-paths-up-linear" },
  mcp: { label: "MCP", icon: "solar:cpu-bolt-linear" },
};

const SEEDED = ["cli", "console", "ci", "sdk", "console", "mcp", "ci"];

export const runViaOf = (run) => {
  if (!run) return RUN_VIA.console;
  if (run.via && RUN_VIA[run.via]) return RUN_VIA[run.via];
  if (run.seededAs === "history") {
    const n = String(run.id || "")
      .split("")
      .reduce((a, c) => a + c.charCodeAt(0), 0);
    return RUN_VIA[SEEDED[n % SEEDED.length]];
  }
  return RUN_VIA.console;
};

/*
  What a CI run carries: the pull request and commit it ran for, and the pass
  rate it had to clear (--fail-under). Recorded on the run when it starts;
  seeded CI runs get stable ones from their id.
*/
export const ciOf = (run) => {
  if (runViaOf(run).label !== "CI") return null;
  if (run.ci) return run.ci;
  const n = String(run.id || "")
    .split("")
    .reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7);
  return {
    pr: 300 + (n % 200),
    sha: n.toString(16).padStart(7, "0").slice(0, 7),
    branch: [
      "fix/refund-proration",
      "feat/escalation-copy",
      "chore/prompt-tune",
    ][n % 3],
    failUnder: 0.9,
  };
};
