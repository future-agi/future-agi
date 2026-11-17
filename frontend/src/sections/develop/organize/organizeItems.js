// The organise doors' items, in order: the `Organize ▾` menu labels and the
// ⌘K command labels (W9-SCOPE §5 A1, A6, A7). Pure.
import { isSurfaceShown } from "src/launch/launchSwitch";
import { notOfferedLine } from "src/sections/common/Collab/notOffered";
import { isRealBackend } from "src/sections/develop/realBackendCaps";

export const ORGANIZE_ITEMS = Object.freeze([
  {
    kind: "metadata",
    surface: "list.organize.metadata",
    label: "Metadata…",
    command: "Set metadata…",
    icon: "mdi:tag-text-outline",
  },
  {
    kind: "folder",
    surface: "list.organize.folders",
    label: "Move to folder…",
    command: "Move to folder…",
    icon: "mdi:folder-move-outline",
  },
  {
    kind: "agents",
    surface: "list.organize.agents",
    label: "Agents…",
    command: "Attach to agents…",
    icon: "mdi:robot-outline",
  },
]);

/** Why a read-only person can't organise. */
export const ORGANIZE_READ_ONLY = "Viewers can't organise datasets";

/** Each Organize door claims independently; visibility is not server support. */
export const offeredOrganizeItems = () =>
  ORGANIZE_ITEMS.filter((item) => isSurfaceShown(item.surface));

/** The calm unavailable line, or null when this door can actually act. */
export const organizeItemOff = (item, metadataCapability) => {
  if (item.kind === "metadata" && isRealBackend()) {
    if (metadataCapability?.isError)
      return "Could not check metadata availability. Reload to try again.";
    if (metadataCapability?.isLoading)
      return "Checking metadata availability…";
    return metadataCapability?.allowed === true
      ? null
      : "Metadata is not available on this server.";
  }
  // Preserve the prototype and the independent folder/agent gates.
  return notOfferedLine(item.surface);
};
