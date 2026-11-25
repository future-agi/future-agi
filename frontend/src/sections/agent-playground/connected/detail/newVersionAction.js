// How a new version arrives for this agent: a code agent syncs its branch (or
// takes a new upload), a provider agent syncs the provider, a phone or LiveKit
// agent can't be re-read so its prompt and settings are edited by hand, a
// built-in agent takes an edited prompt. `hint` says what the button does.
export const newVersionAction = (agent) => {
  if (agent.source === "archive") {
    return {
      label: "Upload new version",
      icon: "solar:cloud-upload-linear",
      kind: "upload",
      hint: "Upload the new folder; we read it and save it as the next version",
    };
  }
  if (agent.source === "github") {
    return {
      label: "Sync",
      icon: "solar:refresh-linear",
      kind: "sync",
      hint: "Sync from GitHub and save any changes as a new version",
    };
  }
  if (agent.source === "vapi" || agent.source === "retell") {
    return {
      label: "Sync",
      icon: "solar:refresh-linear",
      kind: "sync",
      hint: `Sync from ${agent.source === "vapi" ? "Vapi" : "Retell"} and save any changes as a new version`,
    };
  }
  if (agent.source === "phone" || agent.source === "livekit") {
    return {
      label: "Add version",
      icon: "solar:add-circle-linear",
      kind: "edit",
      hint: "We can't sync this agent; record what changed as a new version",
    };
  }
  return {
    label: "Add version",
    icon: "solar:add-circle-linear",
    kind: "manual",
    hint: "Save an edited prompt as a new version",
  };
};
