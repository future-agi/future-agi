import { AGENT_TYPES } from "src/sections/agents/constants";

// The agent-type picker on the Hosted-platform panel. Voice and Chat are live;
// the remaining modalities are gated until their rosters ship.
export const ENTRY_AGENT_TYPES = [
  {
    id: AGENT_TYPES.VOICE,
    label: "Voice",
    icon: "solar:phone-calling-rounded-linear",
  },
  { id: AGENT_TYPES.CHAT, label: "Chat", icon: "solar:chat-round-linear" },
  {
    id: "computer",
    label: "Computer use",
    icon: "solar:monitor-linear",
    comingSoon: true,
  },
  {
    id: "code",
    label: "Code",
    icon: "solar:code-square-linear",
    comingSoon: true,
  },
  {
    id: "robotics",
    label: "Robotics",
    icon: "solar:cpu-bolt-linear",
    comingSoon: true,
  },
];

// Modality metadata for the My Environments agent-type cell. Keyed by the same
// ids the flattened rows carry (chat === AGENT_TYPES.CHAT === "text").
export const MODALITY = {
  [AGENT_TYPES.VOICE]: {
    id: AGENT_TYPES.VOICE,
    label: "Voice",
    icon: "solar:microphone-3-linear",
  },
  [AGENT_TYPES.CHAT]: {
    id: AGENT_TYPES.CHAT,
    label: "Chat",
    icon: "solar:chat-round-line-linear",
  },
  computer: {
    id: "computer",
    label: "Computer use",
    icon: "solar:monitor-linear",
  },
  code: { id: "code", label: "Code", icon: "solar:code-square-linear" },
  robotics: {
    id: "robotics",
    label: "Robotics",
    icon: "solar:bicycling-linear",
  },
  world: { id: "world", label: "Games & worlds", icon: "solar:gamepad-linear" },
  tools: {
    id: "tools",
    label: "Tools & protocols",
    icon: "solar:widget-6-linear",
  },
};

// Shown when a row's agent type is missing or unrecognised. We deliberately do
// NOT fall back to Chat — an unknown modality is not a chat agent, and labelling
// it "Chat" misreports repo/upload/other envs whose type the backend hasn't set.
export const UNIDENTIFIED_MODALITY = {
  id: "unidentified",
  label: "Not identified",
  icon: "solar:question-circle-linear",
};

export const CALL_DIRECTION = { INBOUND: "inbound", OUTBOUND: "outbound" };

export const CALL_DIRECTION_LABEL = {
  [CALL_DIRECTION.INBOUND]: "Inbound: we call your agent",
  [CALL_DIRECTION.OUTBOUND]: "Outbound: your agent dials us",
};
