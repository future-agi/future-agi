import PropTypes from "prop-types";

// Shared constants for the voice detail drawer.

// Display labels for the talk-ratio legend (matches the search filter tabs,
// which show `user` as "Customer"). Override via the `roleLabels` prop.
export const DEFAULT_ROLE_LABELS = {
  user: "Customer",
  assistant: "Assistant",
  system: "System",
  tool: "Tool",
};

export const LEFT_PANEL_TAB_SHAPE = PropTypes.shape({
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  icon: PropTypes.string,
  content: PropTypes.node,
  // Stays mounted, hidden, on other tabs, so what the reader opened in it survives a tab change.
  keepMounted: PropTypes.bool,
});
