import { create } from "zustand";

export const useCreateWorkspaceModal = create((set) => ({
  open: false,
  setOpen: (value) => set({ open: value }),
}));

export const useCreateOrganizationModal = create((set) => ({
  open: false,
  setOpen: (value) => set({ open: value }),
}));

export const useSettingsOpen = create((set) => ({
  settingOpen: false,
  setSettingOpen: (value) => set({ settingOpen: value }),
}));

/**
 * L7c g1 (cmd:go.*): the Settings sidebar belongs to /dashboard/settings
 * pages only. The navs follow the URL both ways, so a ⌘K jump (or any link)
 * out of Settings brings the app nav back, not only `Back to app`.
 */
export const isSettingsPath = (pathname) =>
  /^\/dashboard\/settings(\/|$)/.test(String(pathname ?? ""));

/** The nav's settings-open state for a path, and the stale Back-to-app key. */
export function syncSettingsOpen(pathname, setSettingOpen) {
  const inSettings = isSettingsPath(pathname);
  setSettingOpen(inSettings);
  if (!inSettings) {
    try {
      window.localStorage.removeItem("redirect-url-from-settings");
    } catch {
      /* storage blocked */
    }
  }
}
