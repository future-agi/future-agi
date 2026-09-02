/**
 * Classify one assistant turn's tool calls for display.
 *
 * The agent loop self-corrects: it calls a tool, reads the validation error,
 * and calls it again with the right arguments. Both attempts arrive as tool
 * calls and the failed one used to render in red, so a turn that worked
 * perfectly could show seven red cards and no failures.
 *
 * A step is a retry, not a failure, when either
 *   - a later step called the same tool and it completed, or
 *   - the tool name did not exist, which the loop always follows with the
 *     real name.
 * Everything else is a real failure and stays visible.
 */

const TOOL_NOT_FOUND = /^Tool '[^']*' not found/;

export function classifySteps(toolCalls = []) {
  const succeededLater = new Map();
  toolCalls.forEach((tc, i) => {
    if (tc.status === "completed") {
      succeededLater.set(tc.tool_name, i);
    }
  });

  return toolCalls.map((tc, i) => {
    if (tc.status !== "error") return { ...tc, outcome: tc.status };
    const recoveredAt = succeededLater.get(tc.tool_name);
    const recovered = recoveredAt !== undefined && recoveredAt > i;
    const badName = TOOL_NOT_FOUND.test(tc.result_summary || "");
    return { ...tc, outcome: recovered || badName ? "retried" : "error" };
  });
}

// The live line reads as an activity, not as an identifier. Tool names are already
// verb_noun, so the verb carries almost all of it.
const VERBS = {
  add: "Adding",
  analyze: "Analyzing",
  check: "Checking",
  create: "Creating",
  delete: "Deleting",
  explore: "Exploring",
  get: "Reading",
  list: "Listing",
  read: "Reading",
  render: "Rendering",
  rerun: "Re-running",
  run: "Running",
  save: "Saving",
  search: "Searching",
  submit: "Submitting",
  test: "Testing",
  update: "Updating",
};

export function humanize(toolName = "") {
  const parts = String(toolName).split("_").filter(Boolean);
  if (!parts.length) return "Working";
  const verb = VERBS[parts[0]];
  if (!verb) return toolName.replace(/_/g, " ");
  const rest = parts.slice(1).join(" ");
  return rest ? `${verb} ${rest}` : verb;
}

export function trailSummary(steps = []) {
  const running = steps.filter((s) => s.outcome === "running");
  const failed = steps.filter((s) => s.outcome === "error");
  const retried = steps.filter((s) => s.outcome === "retried");
  return {
    total: steps.length,
    running: running.length,
    failed: failed.length,
    retried: retried.length,
    current: running.length ? running[running.length - 1] : null,
  };
}

/** "1m 12s" / "8s". Never "0s", which reads as broken. */
export function formatElapsed(ms) {
  const total = Math.max(1, Math.round(ms / 1000));
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

/**
 * Collapse each run of consecutive tool_call blocks into one trail block, so
 * the message renders as text, one trail line, text, not as forty cards.
 */
export function groupBlocks(blocks = []) {
  const grouped = [];
  blocks.forEach((block) => {
    if (block.type !== "tool_call") {
      grouped.push(block);
      return;
    }
    const last = grouped[grouped.length - 1];
    if (last && last.type === "trail") {
      last.toolCalls.push(block.toolCall);
      return;
    }
    grouped.push({
      type: "trail",
      id: `trail-${block.id}`,
      toolCalls: [block.toolCall],
    });
  });
  return grouped;
}

/**
 * A skill declares the flow it intends to follow in example_trajectories, so
 * the trail does not have to guess: it can say which declared step the run is
 * on instead of only relabelling the tool that already fired.
 *
 * A trajectory is { user, steps: [{ tool, params }] }. Only the ordered tool
 * names matter here.
 */
export function declaredSteps(trajectory) {
  return (trajectory?.steps || []).map((s) => s?.tool).filter(Boolean);
}

/**
 * A skill may declare several flows for several kinds of request. Pick the one
 * that explains the most of what actually ran; before anything has run, the
 * longest declared flow is the best description of the job.
 */
export function pickTrajectory(trajectories = [], toolCalls = []) {
  const usable = (trajectories || []).filter((t) => declaredSteps(t).length);
  if (!usable.length) return null;
  if (usable.length === 1) return usable[0];

  const ran = (toolCalls || []).map((tc) => tc?.tool_name);
  let best = null;
  let bestScore = -1;
  usable.forEach((t) => {
    const plan = declaredSteps(t);
    const declared = new Set(plan);
    const hits = ran.filter((n) => declared.has(n)).length;
    const score = hits * 1000 + plan.length;
    if (score > bestScore) {
      bestScore = score;
      best = t;
    }
  });
  return best;
}

export function planFor(trajectories = [], toolCalls = []) {
  return declaredSteps(pickTrajectory(trajectories, toolCalls));
}

/**
 * Walk the live tool calls against the declared flow.
 *
 * A run does not walk its plan cleanly, so every call gets one of three
 * readings and none of them is dropped:
 *   plan       the next declared occurrence of this tool, at or after the
 *              cursor. Advances the run.
 *   revisit    a declared step the run already passed, either because the tool
 *              is being called again or because the plan was taken out of
 *              order. Counts as reached, never moves the cursor backwards.
 *   extra      the tool is nowhere in the declared flow.
 *
 * `done` counts distinct declared steps that were actually reached, so a
 * jumped step is never counted as run, and `pending` names the ones that were
 * not.
 */
export function alignToPlan(toolCalls = [], plan = []) {
  const planned = plan.length;
  const empty = {
    steps: [],
    byCallId: {},
    planned,
    done: 0,
    extra: 0,
    pending: [],
  };
  if (!planned) return { ...empty, steps: toolCalls };

  const reached = new Set();
  const lastSeen = new Map();
  const byCallId = {};
  let cursor = 0;
  let extra = 0;

  const steps = (toolCalls || []).map((call) => {
    const name = call?.tool_name;
    let aligned;
    const ahead = plan.indexOf(name, cursor);
    if (ahead !== -1) {
      cursor = ahead + 1;
      aligned = { ...call, planIndex: ahead, planKind: "plan" };
    } else {
      const known = lastSeen.has(name)
        ? lastSeen.get(name)
        : plan.indexOf(name);
      if (known >= 0) {
        aligned = { ...call, planIndex: known, planKind: "revisit" };
      } else {
        extra += 1;
        aligned = { ...call, planIndex: null, planKind: "extra" };
      }
    }
    if (aligned.planIndex !== null) {
      reached.add(aligned.planIndex);
      lastSeen.set(name, aligned.planIndex);
    }
    byCallId[call?.call_id] = aligned;
    return aligned;
  });

  const pending = [];
  plan.forEach((tool, index) => {
    if (!reached.has(index)) pending.push({ index, tool });
  });

  return { steps, byCallId, planned, done: reached.size, extra, pending };
}

/**
 * Which skill a turn ran under, read off the message that triggered it. This
 * mirrors the backend's own parse exactly (startswith "/", first space
 * delimited token), so the trail can never name a skill the turn did not run.
 */
export function slugFromMessage(content) {
  const text = String(content || "");
  if (!text.startsWith("/")) return null;
  return text.split(" ", 1)[0].slice(1) || null;
}
