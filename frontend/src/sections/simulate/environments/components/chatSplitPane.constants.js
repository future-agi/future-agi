export const CHAT_PANE_DEFAULT_WIDTH = 400;
export const CHAT_PANE_MIN_WIDTH = 320;
export const CHAT_PANE_MAX_WIDTH = 640;
// Dragging the chat wider stops here for the right pane; only the chat's own
// minimum wins over it, in a container narrower than the two together.
export const CHAT_PANE_MIN_RIGHT_WIDTH = 480;
export const CHAT_PANE_RAIL_WIDTH = 44;
// The line between the chat and the right pane (the chat column's border).
export const CHAT_PANE_DIVIDER_WIDTH = 1;
// A pointer has to move this far before a press on the divider becomes a drag,
// so a click with a small wobble doesn't resize (or save) anything.
export const CHAT_PANE_DRAG_THRESHOLD = 3;
export const CHAT_PANE_KEY_STEP = 12;
export const CHAT_PANE_KEY_STEP_LARGE = 40;

export const CHAT_PANE_STORAGE_KEYS = {
  width: "simEnv.chatPane.width",
  collapsed: "simEnv.chatPane.collapsed",
};

export const CHAT_PANE_COPY = {
  open: "Open chat",
  expand: "Expand chat",
  resize: "Resize chat",
};
