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

// Who someone on the call other than the agent and the customer is, when the call kept no name.
export const OTHER_PARTY_FALLBACK = "Someone";
export const CALL_PARTICIPANTS_HEADING = "On the call";
export const WORLD_ACTION_DETAILS = "Details";

export const LEFT_PANEL_TAB_SHAPE = PropTypes.shape({
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  icon: PropTypes.string,
  content: PropTypes.node,
});

// How a sub-actor joins a call, as the transcript and the contract tab both name it.
export const SUB_ACTOR_CHANNEL_LABELS = {
  audio_mix: "Heard nearby",
  audio_handoff: "Takes the phone",
  ivr: "Phone menu",
};
