import { getChipConfig } from "src/components/scenarios/CustomCellRenderers/ChipCellRenderer";

// Cell text for the Connected agents table. Known values get their proper
// spelling; anything else the API sends is shown humanised, never dropped.

const EMPTY = "—";

const MODALITY_LABELS = { text: "Chat" };

const SOURCE_LABELS = {
  github: "GitHub",
  archive: "Upload",
  livekit: "LiveKit",
  vapi: "Vapi",
  retell: "Retell",
  bland: "Bland.ai",
  eleven_labs: "ElevenLabs",
};

const enumField = (id, label, labels) => ({
  id,
  label,
  type: "enum",
  multiple: true,
  options: Object.entries(labels).map(([value, text]) => ({
    value,
    label: text,
  })),
  operators: ["in"],
});

export const CONNECTED_AGENT_FILTER_FIELDS = [
  enumField("agent_type", "Type", { voice: "Voice", text: "Chat" }),
  enumField("source", "Source", SOURCE_LABELS),
  enumField("environments", "Environments", {
    with: "In an environment",
    without: "No environment yet",
  }),
  enumField("observe", "Observe project", {
    linked: "Linked",
    not_linked: "Not linked",
  }),
];

const FILTER_IDS = CONNECTED_AGENT_FILTER_FIELDS.map((field) => field.id);

export const filtersToRows = (filters) =>
  FILTER_IDS.filter((id) => filters?.[id]?.length).map((id) => ({
    field: id,
    fieldType: "string",
    operator: "in",
    value: filters[id],
  }));

export const rowsToFilters = (rows) => {
  const result = {};
  (rows || []).forEach((row) => {
    if (!FILTER_IDS.includes(row.field)) return;
    const values = [].concat(row.value ?? []).filter((v) => v !== "");
    if (!values.length) return;
    result[row.field] = [...new Set([...(result[row.field] ?? []), ...values])];
  });
  return Object.keys(result).length ? result : null;
};

export const humanize = (value) =>
  String(value)
    .split(/[_-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

export const modalityLabel = ({ modality, direction }) => {
  if (!modality) return EMPTY;
  const label = MODALITY_LABELS[modality] || humanize(modality);
  return direction ? `${label} · ${humanize(direction)}` : label;
};

// The icon the old Agent Definition list showed for the same modality.
const MODALITY_ICON_KEYS = { voice: "voice", text: "chat", chat: "chat" };
export const modalityIcon = ({ modality, direction }) => {
  const key = MODALITY_ICON_KEYS[modality];
  if (!key) return null;
  return getChipConfig(direction ? `${key}_${direction}` : key).icon ?? null;
};

export const sourceLabel = (source) =>
  source ? SOURCE_LABELS[source] || humanize(source) : EMPTY;

// `environments` holds at most five; `environmentCount` is the true total.
export const usedInLabel = ({ environments, environmentCount }) => {
  if (!environmentCount) return { text: "No environments yet", empty: true };
  if (environmentCount === 1) {
    return { text: environments?.[0]?.name || "1 environment", empty: false };
  }
  return { text: `${environmentCount} environments`, empty: false };
};

const agentsCount = (count) => `${count} agent${count === 1 ? "" : "s"}`;

export const deleteResultMessage = (deleted) =>
  `${agentsCount(deleted)} deleted.`;

export const agentNames = (agents) => {
  const names = agents.map((agent) => agent.name);
  if (names.length > 3) {
    return `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
  }
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
};

export const deleteConfirmText = (agents) =>
  `This deletes ${agentNames(agents)} with all ${
    agents.length === 1 ? "its" : "their"
  } versions.`;

export const deleteBlockedReason = (agents) => {
  const environments = agents.reduce(
    (sum, agent) => sum + (agent.environmentCount ?? 0),
    0,
  );
  return `${agentNames(agents)} ${agents.length === 1 ? "is" : "are"} still used in ${
    environments === 1 ? "an environment" : "environments"
  }. Delete those environments first.`;
};

export const versionsParts = ({ latestLabel, versionCount }) => {
  if (!latestLabel) return { version: "", rest: EMPTY };
  return {
    version: latestLabel,
    rest: ` · ${versionCount} total`,
  };
};
