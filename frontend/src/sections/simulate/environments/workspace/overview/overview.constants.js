import PropTypes from "prop-types";
import { getPacks, getRows } from "src/api/simulate-environments/_fixtures/scenarioPool";
import { ORIGIN_ID } from "../../buildEnvironment/provenance.constants";

// Every copy string the Overview surface renders.
export const OVERVIEW_COPY = {
  // "Attach agent" (no agent yet) stays deferred behind this tooltip; "Manage
  // versions" is now live and opens the AgentsPanel drawer.
  agentVersionsSoon: "Agent versions land with the next phase",
  manageVersionsSubtitle: "Manage the agent versions running against this environment",
  facts: {
    channel: "Channel",
    domain: "Domain",
    transports: "Transports",
    scenarioPacks: "Scenario packs",
  },
  capabilities: "Capabilities",
  world: "The world",
  toolsTitle: "Tools",
  toolsConnected: (n) => `${n} actions your agent declared, with the arguments it really takes`,
  toolsDisconnected: "Read from your agent once it connects",
  noToolsTitle: "No tools yet",
  noToolsBody: "Tool definitions come from the agent.",
  connectAgent: "Connect agent",
  noArguments: "no arguments",
  hardRulesTitle: "Hard rules",
  hardRulesSubtitle: "Told to the agent, graded afterwards — hover a badge for where it was found",
  noRules: "No rules yet — add them on the Contract tab or as the agent introduces them.",
  useCasesTitle: "Use cases",
  useCasesSubtitle: "What it is actually for",
  amendmentsTitle: "Amendments",
  amendmentsSubtitle: "Changed after reading, each with its reason",
  seededTitle: "Seeded data",
  dependsTitle: "What it depends on",
  dependsSubtitle: "Built and torn down with the environment",
  seedBlurb: (rows) =>
    `${rows.toLocaleString()} rows that fill this environment before your agent arrives — the world it actually works in. Rebuilt for every task, so nothing carries over.`,
  usedBy: (name) => `used by ${name}`,
};

// The agent summary (test-subject) card copy.
export const AGENT_SUMMARY_COPY = {
  label: "Test subject",
  connected: "Connected",
  noAgentTitle: "No agent attached",
  agentTitle: (label) => `Agent ${label}`,
  lockedBody:
    "Seeded baseline shipped with this template. Fork the environment to add your own agent versions.",
  attachedBody: (count, endpoint) =>
    `${count} version${count === 1 ? "" : "s"} on record${endpoint ? ` · ${endpoint}` : ""}. This environment stays put — swap in another agent to compare.`,
  portableBody:
    "This environment is portable. Attach an agent as the test subject; you can swap in different agents later without rebuilding the env.",
  fork: "Fork to edit",
  manage: "Manage versions",
  attach: "Attach agent",
};

// The environment shape Overview reads. Local PropTypes.shape (Phase-2 owns the
// shared workspace.shapes.js, which this task does not touch).
export const ENV_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  name: PropTypes.string,
  surface: PropTypes.string,
  domain: PropTypes.string,
  description: PropTypes.string,
  tools: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      desc: PropTypes.string,
      args: PropTypes.arrayOf(PropTypes.string),
    })
  ),
  rules: PropTypes.arrayOf(PropTypes.string),
  seed: PropTypes.shape({
    tables: PropTypes.arrayOf(
      PropTypes.shape({
        name: PropTypes.string,
        rows: PropTypes.number,
        note: PropTypes.string,
      })
    ),
  }),
});

export const ENV_STATE_SHAPE = PropTypes.shape({
  agent: PropTypes.shape({
    via: PropTypes.string,
    values: PropTypes.shape({
      sdkEndpoint: PropTypes.string,
      endpoint: PropTypes.string,
    }),
  }),
  agentVersions: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.string, note: PropTypes.string })
  ),
  activeAgentVersion: PropTypes.string,
  scenarios: PropTypes.arrayOf(
    PropTypes.shape({
      persona: PropTypes.shape({ slug: PropTypes.string, name: PropTypes.string }),
    })
  ),
  evals: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string })),
  runs: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string })),
  // The env version the world was last re-derived against a given agent, plus
  // the version history the refresh banner appends to.
  envDerivedForAgent: PropTypes.string,
  envVersions: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string })),
  activeEnvVersion: PropTypes.string,
  // A keyed map of tool name → the effect the reader confirmed for it.
  toolResolutions: PropTypes.objectOf(PropTypes.string),
});

// The environment in force for this Overview: depth (difficulty) is editable on
// the Scenarios step, so report what is set rather than what the world shipped
// with. Ports the designer's effectiveEnv (rlContract.js) without owning it.
export const effectiveEnv = (env, envState) => ({
  ...env,
  difficulty: envState?.difficulty || env?.difficulty,
});

// Pack + scenario counts for the Fact strip. Ports the designer's packStats
// (scenarios.js) over the exported getPacks/getRows so the fixture stays owned
// by its own module.
export const packStatsFor = (env) => {
  const packs = getPacks(env);
  return {
    packs: packs.length,
    scenarios: packs.reduce((total, pack) => total + getRows(pack.id, env).length, 0),
  };
};

// Where each hard rule was found. The world carries rules as plain strings, so
// the origin is derived the way the designer's provenance.js did: the first two
// rules are enforced in code, the last of a reasonably sized set is prose-only
// (the held case the mechanism exists for), the rest are prompt-only. Uses the
// existing Phase-2 ORIGIN_KINDS ids — no new tones. A rule with no derivable
// origin falls back to prompt (recorded but ungraded, the safe semantic).
export const ruleOriginFor = (index, ruleCount) => {
  if (index < 2) return ORIGIN_ID.CODE;
  if (ruleCount >= 4 && index === ruleCount - 1) return ORIGIN_ID.DOC;
  return ORIGIN_ID.PROMPT;
};

const ORIGIN_FILES = {
  [ORIGIN_ID.CODE]: "agent/policy.py",
  [ORIGIN_ID.PROMPT]: "prompts/system.md",
  [ORIGIN_ID.DOC]: "vendor/support-kit/README.md",
};

const hashSeed = (s = "") => {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
};

const lineFor = (seed, i) => 12 + ((seed + i * 37) % 180);

// The rule rows Overview renders: subject + origin + file:line for the chip.
export const ruleRowsFor = (env) => {
  const seed = hashSeed(env?.id || "env");
  const rules = env?.rules || [];
  return rules.map((subject, i) => {
    const origin = ruleOriginFor(i, rules.length) || ORIGIN_ID.PROMPT;
    return {
      id: `rule-${i}`,
      subject,
      origin,
      file: ORIGIN_FILES[origin] || ORIGIN_FILES[ORIGIN_ID.PROMPT],
      line: lineFor(seed, i + 7),
    };
  });
};

// The "agent moved ahead" refresh banner copy.
export const REFRESH_COPY = {
  title: (label) => `Agent ${label} is newer than this environment`,
  body: (label) =>
    `Optional — re-derive the world against ${label}, or keep running the current one.`,
  action: "Refresh environment",
  note: (label) => `Re-derived against agent ${label}`,
};

// The getting-started checklist copy. Steps are static prose; the counts and
// env name are folded in at render.
export const CHECKLIST_COPY = {
  title: "Next steps",
  subtitle: (done, total) =>
    `${done} of ${total} complete — ${total - done} left to run your first simulation`,
  created: {
    title: "Environment created",
    body: (name) => `${name} is set up and ready for configuration.`,
  },
  agent: {
    title: "Connect an agent",
    body: "Wire your agent so it can act against the environment. This flow lets you connect one after the fact.",
    cta: "Connect agent",
  },
  scenarios: {
    title: "Add scenarios",
    body: "Tasks the agent has to complete. Add a few concrete cases the run will grade against.",
    cta: (n) => (n > 0 ? `${n} added — add more` : "Add scenarios"),
  },
  evals: {
    title: "Add evaluations",
    body: "Graders that decide whether each run passed. Pick from the library or author your own.",
    cta: (n) => (n > 0 ? `${n} added — add more` : "Add evaluations"),
  },
  run: {
    title: "Run your first simulation",
    body: "Kick off a run — you'll see the transcript, tool calls, and eval verdicts land in real time.",
    cta: "Run simulation",
  },
};

