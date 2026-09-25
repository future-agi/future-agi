// An error the loop recovered from is a retry, not a failure.
export const RETRIED = "retried";

// How a call read against the declared flow.
export const STEP = { PLAN: "plan", REVISIT: "revisit", EXTRA: "extra" };

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
    return { ...tc, outcome: recovered || badName ? RETRIED : "error" };
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
  const retried = steps.filter((s) => s.outcome === RETRIED);
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

// Consecutive tool calls become one trail block; text between them splits it.
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

export function declaredSteps(trajectory) {
  return (trajectory?.steps || []).map((s) => s?.tool).filter(Boolean);
}

// Of several declared flows, the one explaining the most of what actually ran.
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

// Every call reads as plan, revisit or extra. A revisit never moves the cursor
// backwards, and `done` counts only declared steps actually reached.
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
      aligned = { ...call, planIndex: ahead, planKind: STEP.PLAN };
    } else {
      const known = lastSeen.has(name)
        ? lastSeen.get(name)
        : plan.indexOf(name);
      if (known >= 0) {
        aligned = { ...call, planIndex: known, planKind: STEP.REVISIT };
      } else {
        extra += 1;
        aligned = { ...call, planIndex: null, planKind: STEP.EXTRA };
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

// The same parse the consumer uses, so the trail cannot name a skill that never ran.
export function slugFromMessage(content) {
  const text = String(content || "");
  if (!text.startsWith("/")) return null;
  return text.split(" ", 1)[0].slice(1) || null;
}
