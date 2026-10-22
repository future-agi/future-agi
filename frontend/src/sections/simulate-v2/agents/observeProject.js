/*
  The agent's Observe project — one per agent (RSI PRD, Agents → Links:
  "The Observe project whose traces or call logs feed the Error Feed").
  It's what lets Fix find the agent behind an Error Feed issue. Set from the
  agent's side only; Change swaps it for another project with no agent yet.
*/
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { useConnectedAgents, useSimStore } from "../store";
import { knownProjects, projectsOfAgent } from "../_mock/projectLinks";
import { useErrorFeedStore } from "src/pages/dashboard/error-feed/store";

/* A link set on the agent wins over one read from its call logs or traces. */
const RANK = { set: 0, watching: 1, seeded: 2 };

/** The one project of an agent, or null — usable outside React. */
export const observeProjectOf = (agent, state, agents = []) => {
  const all = projectsOfAgent(agent, state, agents);
  return [...all].sort((a, b) => (RANK[a.how] ?? 3) - (RANK[b.how] ?? 3))[0] || null;
};

/**
 * A new project linked to this agent that hasn't had its first trace yet:
 * { project, since, via } or null. Only while the link is still this
 * agent's — a project another agent has since taken isn't pending here.
 */
export const pendingProjectOf = (agent, state, agents = []) => {
  const p = agent?.values?.observePending;
  if (!p?.project || agent?.values?.observeChosenAt) return null;
  const owner = state?.projectLinks?.[p.project];
  return owner === agent.key ? p : null;
};

/** Every project still waiting for its first trace — nothing in Observe yet. */
export const pendingProjectIds = (state, agents = []) => new Set(
  agents.map((a) => pendingProjectOf(a, state, agents)?.project).filter(Boolean),
);

/** The TraceAI setup guide — how a code agent starts sending traces. */
export const TRACEAI_DOCS_URL = "https://docs.futureagi.com/docs/observe";

/**
 * { project, options, link(id), unlink(), openInObserve(), viewIssues() }
 *   project — { project, name, how } or null
 *   options — projects with no agent yet, to link or change to
 */
export const useAgentObserveProject = (agent) => {
  const navigate = useNavigate();
  const { state, dispatch } = useSimStore();
  const { agents } = useConnectedAgents();
  const project = useMemo(() => (agent ? observeProjectOf(agent, state, agents) : null), [agent, state, agents]);
  /* Projects waiting for a first trace aren't in Observe yet — not options. */
  const options = useMemo(() => {
    const waiting = pendingProjectIds(state, agents);
    return knownProjects(state, agents).filter((p) => p.link.status !== "linked" && !waiting.has(p.id));
  }, [state, agents]);
  const pending = agent ? pendingProjectOf(agent, state, agents) : null;
  /* Linked by hand on this agent — the links Change replaces. */
  const ownLinks = Object.entries(state?.projectLinks || {}).filter(([, k]) => k === agent?.key).map(([p]) => p);
  const unlink = () => ownLinks.forEach((p) => dispatch({ type: "linkProjectAgent", project: p, agentKey: null }));
  const link = (id) => {
    unlink();
    dispatch({ type: "linkProjectAgent", project: id, agentKey: agent.key });
  };
  const viewIssues = () => {
    if (!project) return;
    useErrorFeedStore.getState().setSelectedProject(project.project);
    navigate(paths.dashboard.errorFeed.root);
  };
  const openInObserve = () => navigate(paths.dashboard.observe);
  return { project, pending, options, link, unlink, canUnlink: project?.how === "set", viewIssues, openInObserve };
};
