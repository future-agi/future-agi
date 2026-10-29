// How a new version arrives for this agent: a code agent syncs its branch (or
// takes a new upload), a provider agent syncs the provider, a built-in agent
// takes an edited prompt. An upload's flow is not wired yet.
export const newVersionAction = (agent) => {
  if (agent.source === "archive") {
    return {
      label: "Upload new version",
      icon: "solar:cloud-upload-linear",
      kind: null,
    };
  }
  if (
    agent.agentKind === "source" ||
    agent.source === "github" ||
    agent.source === "vapi" ||
    agent.source === "retell"
  ) {
    return { label: "Sync", icon: "solar:refresh-linear", kind: "sync" };
  }
  return {
    label: "Add version",
    icon: "solar:add-circle-linear",
    kind: "manual",
  };
};
