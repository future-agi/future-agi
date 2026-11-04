// How a new version arrives for this agent: a code agent syncs its branch (or
// takes a new upload), a provider agent syncs the provider, a phone or LiveKit
// agent syncs by its prompt and settings being edited, a built-in agent takes
// an edited prompt.
export const newVersionAction = (agent) => {
  if (agent.source === "archive") {
    return {
      label: "Upload new version",
      icon: "solar:cloud-upload-linear",
      kind: "upload",
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
  if (agent.source === "phone" || agent.source === "livekit") {
    return { label: "Sync", icon: "solar:refresh-linear", kind: "edit" };
  }
  return {
    label: "Add version",
    icon: "solar:add-circle-linear",
    kind: "manual",
  };
};
