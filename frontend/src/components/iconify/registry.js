import { addIcon } from "@iconify/react/offline";

// Only evaluated consumers register their icons. This module never imports data.
const names = new Set();
export function registerOfflineIcon(name, data) {
  addIcon(name, data);
  names.add(name);
}
export const hasRegisteredIcon = (name) => names.has(name);
export const registeredIconNames = () => [...names];
