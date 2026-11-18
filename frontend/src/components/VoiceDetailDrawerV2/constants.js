import PropTypes from "prop-types";

// Shared constants for the voice detail drawer.

// Display labels for the talk-ratio legend (matches the search filter tabs,
// which show `user` as "Customer"). Override via the `roleLabels` prop.
export const DEFAULT_ROLE_LABELS = {
  user: "Customer",
  assistant: "Assistant",
  system: "System",
  tool: "Tool",
  others: "Others",
};

export const OTHER_PARTY_FALLBACK = "Someone";
export const CALL_PARTICIPANTS_HEADING = "On the call";
export const WORLD_ACTION_DETAILS = "Details";

export const LEFT_PANEL_TAB_SHAPE = PropTypes.shape({
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  icon: PropTypes.string,
  content: PropTypes.node,
});

export const ACTOR_CHANNEL_LABELS = {
  audio_mix: "Heard nearby",
  audio_handoff: "Takes the phone",
  ivr: "Phone menu",
  chat_message: "Writes in the chat",
};

// Data-palette series slots.
export const OTHER_PARTY_SERIES = 2;

export const WORLD_ACTOR_SERIES = 4;

export const OTHER_PARTY_LABEL_MAX_WIDTH = 320;
