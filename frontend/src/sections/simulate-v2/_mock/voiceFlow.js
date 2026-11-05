/*
  A simulated call's flow, in the shape the call drawer's Graph and Checklist
  tabs read (PathAnalysisView): `scenario_graph` { nodes, edges } and
  `flow_analysis` { current_path, expected_path, new_nodes, new_edges }.

  A real call gets these from the flow-analysis service; a simulated one has
  no such service, so it is drawn from what the run does have — the
  scenario's path (conversationBranch), the tool calls in the transcript, and
  where the call failed. Steps are the path's nodes; each tool call is noted
  on the step it happened in (placed by where it falls in the call); a failed
  call leaves the path at the step it failed on, into a "missed" node naming
  the critical sub-goal it missed.
*/

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const short = (t, max = 40) =>
  t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t;

/**
 * { scenario_graph, flow_analysis } | null
 *   path       — the scenario's step names, in order
 *   transcript — the drawer transcript (tool rows carry tool_meta)
 *   steps      — the run's turns (failStep indexes these)
 *   failStep   — the turn it failed on, when it failed
 *   failedGoal — the critical sub-goal it missed, in words
 */
export const voiceFlowOf = ({
  path,
  transcript = [],
  steps = [],
  failStep = null,
  failedGoal = null,
  unmeasured = null,
} = {}) => {
  const names = (path || []).filter(Boolean);
  if (names.length < 2) return null;
  const last = names.length - 1;
  /* Inner steps only: a tool isn't called at "start" or the hang-up. */
  const inner = (share) =>
    last < 2 ? 0 : clamp(Math.round(share * last), 1, last - 1);

  const toolsAt = new Map();
  const total = Math.max(1, transcript.length - 1);
  transcript.forEach((row, i) => {
    const name = row?.tool_meta?.name;
    if ((row?.role !== "tool" && row?.speaker_role !== "tool") || !name) return;
    const k = inner(i / total);
    toolsAt.set(k, [...new Set([...(toolsAt.get(k) || []), name])]);
  });

  const nodes = names.map((name, k) => ({
    name,
    type: "conversation",
    isStart: k === 0,
    prompt: toolsAt.get(k)?.length ? `Called ${toolsAt.get(k).join(", ")}` : "",
  }));
  /* An edge leaving a step names the tool called in it, and nothing when
     there's nothing to say (no placeholder). Not at the fork a failed call
     leaves from — two labels there sit on each other, and the step's own
     tooltip still names the tool. */
  const forkAt =
    failStep != null && steps.length
      ? clamp(
          Math.round((failStep / Math.max(1, steps.length - 1)) * last),
          1,
          last,
        ) - 1
      : -1;
  const edges = names.slice(1).map((to, k) => ({
    from: names[k],
    to,
    condition: { prompt: k === forkAt ? "" : toolsAt.get(k)?.join(", ") || "" },
  }));

  let currentPath = names;
  const newNodes = [];
  const newEdges = [];
  let summary = "Followed the scenario's path to the end.";
  if (failStep != null && steps.length) {
    /* Where in the path the failing turn falls — never before the opener. */
    const k = clamp(
      Math.round((failStep / Math.max(1, steps.length - 1)) * last),
      1,
      last,
    );
    const failName = `missed: ${short(failedGoal || "a critical sub-goal")}`;
    currentPath = [...names.slice(0, k), failName];
    newNodes.push({
      name: failName,
      type: "conversation",
      prompt: failedGoal ? `Failed here — ${failedGoal}` : "Failed here",
    });
    newEdges.push({
      from: names[k - 1],
      to: failName,
      condition: { prompt: "left the path" },
    });
    summary = `Left the path after ${names[k - 1]}${failedGoal ? `: ${failedGoal}` : ""}.`;
  }

  /* No verdict (the call dropped, or nothing could be graded): no step is
     called addressed or missed — the checklist says what's missing. */
  if (unmeasured)
    summary = `Unmeasured — ${unmeasured}. No step is scored, for or against the agent.`;
  return {
    scenario_graph: { nodes, edges },
    flow_analysis: {
      ...(unmeasured && { unmeasured: true }),
      current_path: currentPath,
      expected_path: names,
      new_nodes: newNodes,
      new_edges: newEdges,
      analysis_summary: summary,
    },
  };
};
