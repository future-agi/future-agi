/**
 * Adding a new agent version.
 *
 * The environment already outlives any one agent version — scenarios belong to
 * the environment, and a run is env × agent. This is the other half: pulling a
 * newer agent in, seeing exactly what changed, and then making the one choice
 * that decides what the next number means.
 *
 *   RE-DERIVE   the contract changed, so the suite should change with it.
 *               New scenarios cover the new surface. Scores are NOT comparable
 *               to the previous version.
 *
 *   FREEZE      run the identical suite against the new agent. The delta is
 *               attributable to the agent and nothing else. This is a
 *               regression test, and it is the honest default.
 *
 * Picking re-derive when you meant freeze is the classic way to produce a
 * number that looks like an improvement and is not, so the UI states the
 * consequence rather than presenting two equal buttons.
 */

export const SYNC_SOURCES = [
  { id: "git", label: "Sync from git", blurb: "Pull the latest commit, or a tag you name.", icon: "solar:code-square-linear", placeholder: "main" },
  { id: "endpoint", label: "New endpoint", blurb: "A different deployment of the same agent.", icon: "solar:plug-circle-linear", placeholder: "https://staging.yourapp.com/agent" },
  { id: "upload", label: "Upload a bundle", blurb: "An SDK bundle or a zip of the source.", icon: "solar:upload-square-linear", placeholder: "agent-v4.zip" },
];

export const SYNC_STEPS = [
  "Fetching source",
  "Reading the contract",
  "Diffing against v3",
  "Sealing agver_@v4",
];

/**
 * What actually changed, at contract granularity — the only diff that matters
 * here, since the contract is what scenarios are written against.
 */
export const versionDiff = (env) => {
  const tools = env?.tools || [];
  return {
    ref: "a41c9e2",
    sealed: "agver_@v4",
    added: [
      { kind: "tool", name: tools[2]?.name ? `${tools[2].name}_v2` : "check_status_v2", note: "New argument: `reason` is now required." },
      { kind: "rule", name: "Refunds above £50 need a supervisor", note: "Moved from prompt into code — now enforced, not just graded." },
    ],
    changed: [
      { kind: "tool", name: tools[0]?.name || "verify_identity", note: "Second factor is now mandatory. Was optional in v3." },
    ],
    removed: [
      { kind: "tool", name: "legacy_lookup", note: "Deleted in the source. 3 scenarios reference it." },
    ],
  };
};

/** The consequence of each choice, in scenarios rather than adjectives. */
export const suiteChoices = (env, diff, scenarioCount) => [
  {
    id: "freeze",
    label: "Freeze & regress",
    recommended: true,
    headline: `Run the same ${scenarioCount} scenarios`,
    blurb: "Identical suite, new agent. Any difference in the score is the agent — nothing else moved.",
    consequences: [
      `${scenarioCount} scenarios run unchanged`,
      `${diff.removed.length ? "3 scenarios call a tool that no longer exists — they will fail, which is the correct signal" : "No scenarios are invalidated"}`,
      "Directly comparable to v3",
    ],
    tone: "#16A34A",
  },
  {
    id: "rederive",
    label: "Re-derive the suite",
    headline: `Rewrite against the new contract`,
    blurb: "New scenarios for the new surface, and the invalidated ones dropped.",
    consequences: [
      `+${(diff.added.length || 0) * 4} new scenarios for the added tool and rule`,
      `${diff.removed.length ? "3 scenarios dropped — the tool they call is gone" : "Nothing dropped"}`,
      "NOT comparable to v3 — different suite, different denominator",
    ],
    tone: "#CA8A04",
  },
];

/** Per-scenario delta, after the fact. */
export const scenarioDelta = (env, scenarios) => {
  const sample = (scenarios || []).slice(0, 6);
  const outcomes = ["fixed", "same", "same", "broke", "same", "fixed"];
  return sample.map((s, i) => ({
    id: s.id,
    title: s.title,
    outcome: outcomes[i % outcomes.length],
    before: outcomes[i % outcomes.length] === "fixed" ? "fail" : "pass",
    after: outcomes[i % outcomes.length] === "broke" ? "fail" : "pass",
  }));
};
