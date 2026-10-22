/*
  Agent → Observe project: the one link the RSI loop runs on.

  An Observe project (a trace project) is where a running agent's production
  traces land; Error Feed issues are built from them. The agent records its
  project — RSI PRD, Agents → Links: "The Observe project whose traces or call
  logs feed the Error Feed". It's set from the agent's side only: connecting
  it in Agents (Flow 1's Observe step), connecting a scanned repository from
  Integrations → GitHub, watching a platform agent's call logs, or the
  agent's Connections tab. Nothing picks an agent for a project or an issue.

  Everything else reads it: Fix and Copy for coding agent find the agent
  behind an issue, an environment's "From Error Feed" lists its agent's
  issues, and the issue header shows project → agent.

  An issue whose project no agent records still reaches the Error Feed —
  triage works on any traced app — and its Fix reads "Connect agent to fix"
  (PRD Flow 3): the connect flow opens with the project already linked.

  One project feeds at most one agent (a squad that works as one counts as
  one), and an agent has one Observe project: linking another replaces the
  link it had (observeProject.js's link, the connect flow, Finish setting up).

  Where the link comes from (`how`):
    set        — linked when the agent was connected, or from its Connections tab
    watching   — the agent's own production calls land there
    seeded     — a demo agent that reports into the project
*/
import { OBSERVE_PROJECTS, observeProjectById } from "./connectAgent";
import { observabilityOf } from "./observability";

/* Demo agents that report into these projects even where their saved copy
   doesn't say so. pickup-dispatch is left out on purpose: no agent in Agents
   records it yet, so its issues show Connect agent to fix. */
const SEEDED_PROJECTS = {
  "returns-refunds-line-agent": "livekit:asst_9f2c1188",
  "customer-support-line-agent": "vapi:asst_v1c2f3",
  "airline-rebooking": "repo:https://github.com/skyline-air/rebooking-agent",
  "guest-booking-line": "retell:asst_gb_7k2m",
};

const nameOfProject = (id) => observeProjectById(id)?.name || id;

/**
 * The project's agent: { project, name, status, agentKey, agent, how }.
 *   status: "linked" | "unlinked"
 */
export const projectLinkOf = (project, state, agents = []) => {
  if (!project) return { project: null, name: null, status: "unlinked" };
  const base = { project, name: nameOfProject(project) };
  /* An old "not an agent" marker reads as no agent — the same thing now. */
  const explicit = state?.projectLinks?.[project] === "__not-an-agent__" ? null : state?.projectLinks?.[project];
  const byKey = (k) => agents.find((a) => a.key === k) || null;
  if (explicit) return { ...base, status: "linked", agentKey: explicit, agent: byKey(explicit), how: "set" };
  const own = agents.find((a) => observabilityOf(a).project === project);
  if (own) return { ...base, status: "linked", agentKey: own.key, agent: own, how: "watching" };
  if (SEEDED_PROJECTS[project]) return { ...base, status: "linked", agentKey: SEEDED_PROJECTS[project], agent: byKey(SEEDED_PROJECTS[project]), how: "seeded" };
  return { ...base, status: "unlinked" };
};

/** The projects linked to this agent, with how each is linked — one once
    its link is set; a read-only list for older stored state that held more. */
export const projectsOfAgent = (agent, state, agents = []) => {
  if (!agent) return [];
  const ids = new Set([
    observabilityOf(agent).on ? observabilityOf(agent).project : null,
    ...Object.entries(state?.projectLinks || {}).filter(([, k]) => k === agent.key).map(([p]) => p),
    ...Object.entries(SEEDED_PROJECTS).filter(([, k]) => k === agent.key).map(([p]) => p),
  ].filter(Boolean));
  const all = agents.some((a) => a.key === agent.key) ? agents : [...agents, agent];
  return [...ids]
    .map((p) => projectLinkOf(p, state, all))
    .filter((l) => l.status === "linked" && l.agentKey === agent.key);
};

/** The workspace's known projects — the catalogue plus any set up here. */
export const knownProjects = (state, agents = []) => {
  const ids = new Set([
    ...OBSERVE_PROJECTS.map((p) => p.id),
    ...Object.keys(state?.projectLinks || {}),
    ...agents.map((a) => (observabilityOf(a).on ? observabilityOf(a).project : null)).filter(Boolean),
  ]);
  return [...ids].map((id) => ({ ...(observeProjectById(id) || { id, name: id }), link: projectLinkOf(id, state, agents) }));
};

/* The likeliest agent for a project: the one whose name shares words with it
   ("pickup-dispatch" → "Pickup dispatch agent"). Null when nothing matches. */
export const likelyAgentFor = (project, agents = []) => {
  const words = (x) => new Set(String(x || "").toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 2 && !["agent", "line", "the", "and"].includes(w)));
  const p = words(project);
  let best = null;
  let score = 0;
  agents.forEach((a) => {
    const s = [...words(a.name)].filter((w) => p.has(w)).length;
    if (s > score) { score = s; best = a; }
  });
  return best;
};

/* The words that name a thing — "Pickup dispatch agent" → pickup, dispatch. */
const nameWords = (x) => new Set(String(x || "").toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 2 && !["agent", "assistant", "the", "and", "acme", "inc"].includes(w)));

/**
 * The existing Observe project an agent's traces most likely land in: one
 * with real traces that no other agent has, sharing the most words with the
 * agent's name or repository ("Pickup dispatch agent", acme-inc/pickup-dispatch
 * → pickup-dispatch). Suggested before anyone creates a new, empty project —
 * an agent split from its traces can't fix its own Error Feed issues.
 */
export const likelyProjectFor = (agent, state, agents = []) => {
  if (!agent) return null;
  const want = new Set([...nameWords(agent.name), ...nameWords(agent.definition?.repoUrl || agent.values?.repo || "")]);
  let best = null;
  let score = 0;
  OBSERVE_PROJECTS.forEach((p) => {
    const link = projectLinkOf(p.id, state, agents);
    if (link.status === "linked" && link.agentKey !== agent.key) return;
    const got = [...nameWords(`${p.id} ${p.name}`)].filter((w) => want.has(w)).length;
    if (got > score) { score = got; best = p; }
  });
  return best;
};
