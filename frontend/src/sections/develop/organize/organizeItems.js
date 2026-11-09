// The organise doors' items, in order: the `Organize ▾` menu labels and the
// ⌘K command labels (W9-SCOPE §5 A1, A6, A7). Pure.
import { isSurfaceShown } from "src/launch/launchSwitch";

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

/**
 * Each Organize door claims independently: folders never enable metadata
 * or agents.
 */
export const offeredOrganizeItems = () =>
  ORGANIZE_ITEMS.filter((item) => isSurfaceShown(item.surface));
