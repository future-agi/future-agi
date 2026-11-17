import { CONNECTED_AGENTS_PATH } from "../../agentsTabs";

export const DETAIL_TABS = [
  { id: "overview", label: "Overview" },
  { id: "definition", label: "Definition" },
  { id: "versions", label: "Versions" },
  { id: "environments", label: "Environments" },
];

// Tabs for areas not built in this release: shown, disabled, with the shared
// line beside the name (a short chip, the whole line in its tooltip).
export const UNRELEASED_TABS = ["Constraints", "Improvements", "Connections"];

export const agentDetailPath = (agentId, tab = "overview") =>
  `${CONNECTED_AGENTS_PATH}/${agentId}/${tab}`;

// The version the page shows: `?v=` as given, else the active one. It isn't
// checked against a version list, so a long history needn't be loaded first.
export const resolveVersionId = (raw, activeVersionId) =>
  raw || activeVersionId || null;
