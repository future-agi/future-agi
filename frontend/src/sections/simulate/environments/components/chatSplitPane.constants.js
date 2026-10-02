export const CHAT_PANE_DEFAULT_WIDTH = 400;
export const CHAT_PANE_MIN_WIDTH = 320;
export const CHAT_PANE_MAX_WIDTH = 640;
// The right pane never gets squeezed below this while the chat is dragged wider.
export const CHAT_PANE_MIN_RIGHT_WIDTH = 480;
export const CHAT_PANE_RAIL_WIDTH = 44;
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
