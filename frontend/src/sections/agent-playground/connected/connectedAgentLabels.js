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

const enumField = (value, label, labels) => ({
  value,
  label,
  type: "enum",
  operators: ["is"],
  choices: Object.keys(labels),
  choiceLabels: labels,
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

export const deleteResultMessage = ({ deleted, skipped }) => {
  const parts = [];
  if (deleted) parts.push(`${agentsCount(deleted)} deleted.`);
  if (skipped) {
    parts.push(
      `${agentsCount(skipped)} skipped: still used in ${
        skipped === 1 ? "an environment" : "environments"
      }.`,
    );
  }
  return parts.join(" ");
};

export const versionsParts = ({ latestLabel, versionCount }) => {
  if (!latestLabel) return { version: "", rest: EMPTY };
  return {
    version: latestLabel,
    rest: ` · ${versionCount} total`,
  };
};
