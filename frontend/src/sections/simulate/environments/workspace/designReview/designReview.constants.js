const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

export const REVIEW_STATUS = {
  AWAITING: "awaiting_approval",
  APPROVED: "approved",
  DISCARDED: "discarded",
};

export const REVIEW_COPY = {
  title: "Review the design",
  summary: ({ tables, tools, rules, scenarios }) =>
    `${[
      plural(tables, "record"),
      plural(tools, "tool"),
      plural(rules, "rule"),
      plural(scenarios, "scenario"),
    ].join(" · ")}. Nothing is built until you approve.`,
  approve: "Approve and build",
  discard: "Discard",
  discardTitle: "Discard this design?",
  discardContent:
    "Nothing is built and this environment is removed. The agent stays as it is.",
  approved: "Approved. The build is running from this design.",
  agentChanged:
    "The agent changed after this design was made. Discard it and build again to use the new version.",
  runBlocked: "Approve the design first",
  viewerLocked: "You don't have permission to change or approve this design.",
  loadError: "Couldn't load the design",
  records: "Records",
  tools: "Tools",
  rules: "Rules",
  useCases: "Use cases",
  recordsSource: "From the agent's reading",
  toolsSource: "From the agent's code",
  rulesSource: "From the agent's prompt",
  useCasesSource: "From the agent's reading",
  none: "None in this design",
  removed: "Removed",
  edited: "Edited",
  undo: "Undo",
  remove: (name) => `Remove ${name}`,
  edit: "Edit rule",
  save: "Save",
  cancel: "Cancel",
  ruleLabel: "Rule",
  scenarios: (count) => plural(count, "scenario"),
  columns: (columns) =>
    columns.length ? columns.join(", ") : "No columns read",
  removeTitle: (name) => `Remove ${name}?`,
  removeContent: ({ tools, rules }) =>
    [
      tools.length
        ? `${tools.map((tool) => `${tool.name} ${tool.reason}`).join("; ")}.`
        : "",
      rules.length
        ? `${plural(rules.length, "rule")} name${rules.length === 1 ? "s" : ""} it.`
        : "",
      "Removing it can make those fail in the environment.",
    ]
      .filter(Boolean)
      .join(" "),
  removeAnyway: "Remove anyway",
};
