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
