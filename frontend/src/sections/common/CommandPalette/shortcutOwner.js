// Who takes Cmd/Ctrl+K. The Falcon AI assistant does, wherever its launcher is
// mounted (every dashboard page but the Hub): it opens the assistant, or the
// Falcon AI page where the deployment does not offer it. Only where no
// launcher is mounted (the Console tab, the Hub) does the app's command
// palette take the key. Each mounted palette registers itself as a host; the
// first one mounted is the active one, so two hosts can never show two
// palettes.
const hosts = [];
const launchers = [];

const register = (list, id) => {
  list.push(id);
  return () => {
    const at = list.indexOf(id);
    if (at !== -1) list.splice(at, 1);
  };
};

/** Register a mounted palette; returns the function that removes it. */
export const registerPaletteHost = (id) => register(hosts, id);

/** The host that renders the palette, or null. */
export const activePaletteHost = () => hosts[0] ?? null;

/** Register a mounted Falcon AI launcher; returns its removal function. */
export const registerAssistantLauncher = (id) => register(launchers, id);

/** Whether the assistant takes Cmd/Ctrl+K (a launcher is mounted). */
export const assistantOwnsCommandShortcut = () => launchers.length > 0;

/** Whether the app palette takes this Cmd/Ctrl+K press (any target). */
export function appPaletteOwnsCommandShortcut() {
  return hosts.length > 0 && !assistantOwnsCommandShortcut();
}

// The earlier name of the palette's answer; the assistant no longer asks it.
export const dsxOwnsCommandShortcut = appPaletteOwnsCommandShortcut;
