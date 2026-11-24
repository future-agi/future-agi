export const KEY_SWITCH_DENIED =
  "Only organization admins or the key's creator can turn this key on or off.";

const SWITCH_ACTIONS = new Set(["enable", "disable"]);

export const switchLocked = (key, actionType) =>
  SWITCH_ACTIONS.has(actionType) && key?.can_switch === false;
