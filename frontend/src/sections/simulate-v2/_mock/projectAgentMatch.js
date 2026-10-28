/*
  Which of the agents already in Agents could be the one behind a traced
  project — for "Is it one of your agents?" in Connect agent to fix and on
  the tracing project. Only what the project's traces or call logs actually
  show counts as a reason: the same platform assistant, the same framework,
  a repository named like the traced service, a name that shares words with
  the project. Nothing is linked from this; the person picks.
*/
import { observeProjectById } from "./connectAgent";
import { definitionOf, FRAMEWORK_META, PROVIDER_LABELS } from "./agentRegistry";

/* The words that name a thing — "Pickup dispatch agent" → pickup, dispatch. */
const STOP = new Set([
  "agent",
  "assistant",
  "line",
  "the",
  "and",
  "acme",
  "inc",
  "app",
  "bot",
]);
const wordsOf = (x) =>
  new Set(
    String(x || "")
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter((w) => w.length > 2 && !STOP.has(w)),
  );
const repoTail = (url) =>
  String(url || "")
    .replace(/#.*$/, "")
    .replace(/\.git$/, "")
    .replace(/\/+$/, "")
    .split("/")
    .pop()
    .toLowerCase();
const sameFramework = (a, b) =>
  !!a &&
  !!b &&
  String(a)
    .toLowerCase()
    .replace(/[^a-z]/g, "") ===
    String(b)
      .toLowerCase()
      .replace(/[^a-z]/g, "");

/**
 * Why this agent could be the project's: [{ text, weight }], strongest first.
 * Empty when nothing the traces show points at it.
 */
export const matchReasonsOf = (agent, projectId, projectName = null) => {
  const project = observeProjectById(projectId);
  const traced = project?.traced || null;
  const v = agent?.values || {};
  const d = definitionOf(agent);
  const reasons = [];
  if (traced?.via === "call-logs") {
    if (
      v.provider === traced.provider &&
      v.agentId &&
      v.agentId === traced.agentId
    )
      reasons.push({
        text: `Same ${PROVIDER_LABELS[traced.provider] || traced.provider} assistant ID`,
        weight: 10,
      });
    else if (v.provider === traced.provider)
      reasons.push({
        text: `Also on ${PROVIDER_LABELS[traced.provider] || traced.provider}`,
        weight: 2,
      });
  }
  if (traced?.via === "traceai") {
    const repo = repoTail(d.repoUrl || v.repoUrl);
    if (repo && traced.service && repo === String(traced.service).toLowerCase())
      reasons.push({
        text: `Repository named ${repo}, like the traced service`,
        weight: 6,
      });
    const fw = d.framework || v.framework;
    if (
      sameFramework(fw, traced.framework) ||
      sameFramework(FRAMEWORK_META[fw]?.label, traced.framework)
    )
      reasons.push({
        text: `Same framework (${FRAMEWORK_META[traced.framework]?.label || traced.framework})`,
        weight: 3,
      });
  }
  const p = wordsOf(project?.name || projectName || projectId);
  const shared = [...wordsOf(agent?.name)].filter((w) => p.has(w));
  if (shared.length)
    reasons.push({
      text: "Name matches the project",
      weight: 2 + shared.length,
    });
  return reasons.sort((a, b) => b.weight - a.weight);
};

/** The agents, likeliest first: [{ agent, reasons, score }]. Deleted agents never. */
export const rankAgentsFor = (projectId, agents = [], projectName = null) =>
  agents
    .filter((a) => !a?.deleted?.at)
    .map((agent) => {
      const reasons = matchReasonsOf(agent, projectId, projectName);
      return {
        agent,
        reasons,
        score: reasons.reduce((n, r) => n + r.weight, 0),
      };
    })
    .sort(
      (a, b) =>
        b.score - a.score ||
        String(a.agent.name).localeCompare(String(b.agent.name)),
    );
