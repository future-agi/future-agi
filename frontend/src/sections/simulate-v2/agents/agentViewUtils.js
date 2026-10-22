import { versionNumber } from "../_mock/versions";
import { needsFoundCheck, sourceFromAgentRecord } from "../_mock/agentRegistry";
import { fixDepthOf } from "../_mock/rsiModel";

export const MONO = "ui-monospace, Menlo, monospace";

export const LANG_ICON = {
  yaml: "solar:settings-linear",
  json: "solar:code-square-linear",
  markdown: "solar:document-text-linear",
  python: "solar:code-linear",
  bash: "solar:key-linear",
  doc: "solar:file-text-linear",
};

export const GROUP_ORDER = ["Config", "Prompts", "Code", "Tools", "Knowledge"];

export const ago = (iso) => {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d}d ago`;
};

export const ORIGIN_LABEL = (v, source) => {
  if (v.origin === "sync") return `Synced from ${source.label}`;
  if (v.origin === "manual") return "Recorded by hand";
  if (v.origin === "pre-versioning") return "Recorded";
  if (v.origin === "rollback") return "Roll back";
  if (v.kind === "credentials") return `Credentials only · behaves as ${v.sameAs || "the version before"}`;
  if (v.origin === "push") return `Pushed ${v.commit?.sha || ""}`.trim();
  if (v.origin === "pr") return v.status === "draft" ? `Draft PR${v.prNumber ? ` #${v.prNumber}` : ""} on ${v.commit?.branch || "its branch"}` : `Merged PR #${v.prNumber}`;
  if (v.applied?.length) return "From an improvement";
  if (v.origin === "connect") return "Connected";
  return versionNumber(v.label) === 1 ? "Connected" : "Added manually";
};

/** Runs that tested this agent, across every environment using it. */
export const runsOfAgent = (agent, state) => (state?.myEnvironments || []).flatMap((env) => {
  const es = state?.byEnv?.[env.id];
  const usesNow = agent.usedIn.some((u) => u.envId === env.id);
  return (es?.runs || [])
    /* Its number in the environment, as the runs list prints it: the one it
       was stamped with, else its place in time (a run still going counts
       from when it started). */
    .map((r, _i, all) => {
      if (r.ordinal) return r;
      const when = (x) => new Date(x.finishedAt || x.startedAt || Date.now()).getTime();
      const chrono = all.filter((x) => !x.synthetic).sort((a, b) => when(a) - when(b));
      return { ...r, ordinal: chrono.indexOf(r) + 1 || null };
    })
    /* Only runs that tested THIS agent. A run stamped with an agent belongs
       to that agent even after the environment moved on to another one; an
       unstamped run belongs to whoever the environment tests now. */
    .filter((r) => !r.synthetic && r.agentVersion && (r.agentKey ? r.agentKey === agent.key : usesNow))
    .map((r) => ({ ...r, envId: env.id, envName: env.name }));
}).sort((a, b) => Date.parse(b.finishedAt || 0) - Date.parse(a.finishedAt || 0));

/* Share of measured scenarios that passed — see _mock/runResult.js. */
export const passRate = (r) => {
  if (typeof r?.rate === "number") return r.rate;
  const n = r?.measured ?? r?.total;
  return n ? Math.round(((r.passed || 0) / n) * 100) : null;
};

/* The version a version was built from — its recorded parent, else the one before it. */
export const parentLabel = (versions, label) => {
  const i = versions.findIndex((v) => v.label === label);
  if (i < 0) return null;
  const own = versions[i].basedOnVersion;
  if (own && own !== label && versions.some((v) => v.label === own)) return own;
  return i > 0 ? versions[i - 1].label : null;
};

/* "1 run", "3 runs" — every count on the page reads naturally. */
export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/* A run that started but never finished isn't a result. */
export const isFinishedRun = (r) => r.status !== "running" && r.status !== "queued" && !!(r.finishedAt || r.status === "done" || r.status === "completed");

/* What the builder starts from when an environment is built from an agent —
   the name, version and size picked in BuildEnvironmentDialog. */
export const buildPresetFor = (agent, { name, version, count, channel = null }) => ({
  ...sourceFromAgentRecord(agent),
  /* Built for one of its channels — the world is that channel's. */
  ...(channel && { channel, agentType: channel }),
  scenarioCount: count,
  connectedAgent: agent,
  agentVersion: version || agent.latest,
  envName: name,
  agentName: agent.name,
});

/** "How deep a fix can go", in one line — said plainly for agents we can only call. */
/* Every depth there is, so the row says what else it could be and why. */
export const FIX_DEPTH_OPTIONS = "It depends on how the agent is connected — everything the change policy allows (a repository, or built here) · the settings the platform's API exposes (Vapi, Retell, ElevenLabs…) · changes by your own coding agent (code on your machine) · a suggested fix (a phone number or endpoint we can only call).";
export const REACHED_AT_OPTIONS = "Set by how it's connected — a platform's API and assistant ID, an endpoint URL, a phone number, a LiveKit project, or our sandbox for code built here. A platform agent moves with Move…; an endpoint is edited here.";

export const fixDepthLine = (agent) => {
  const f = fixDepthOf(agent);
  return f.depth === "suggestion" ? "We can only call this agent, so improvements end in a suggested fix" : f.label;
};

/**
 * Whether the agent page shows Finish setting up: an agent connected through
 * the one-step connect, or any agent that isn't tested anywhere yet — until
 * someone hides it. A code agent whose "Check what we found" is still open
 * shows it regardless: that check is needed before it runs.
 */
export const showsSetup = (agent) => !!agent
  && (needsFoundCheck(agent) || (!agent.values?.setupDismissedAt
    && (!!agent.values?.setupStartedAt || !(agent.usedIn || []).length)));

/*
  Where an agent is tested, in one short line. One environment, or two with
  different names, are named; anything more — or names that repeat — is a
  count, with the list a click away on the Environments tab.
*/
export const testedInLine = (envs = []) => {
  const names = envs.map((u) => u.envName).filter(Boolean);
  if (!names.length) return null;
  const distinct = new Set(names).size === names.length;
  if (names.length === 1) return `Tested in ${names[0]}`;
  if (names.length === 2 && distinct) return `Tested in ${names[0]} and ${names[1]}`;
  return `Tested in ${names.length} environments`;
};
