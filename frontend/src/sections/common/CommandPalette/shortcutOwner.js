// One owner for Cmd/Ctrl+K: the app's command palette, on every page and for
// every focused element (grids, fields and the open palette included).
// Each mounted palette registers itself as a host; the first one mounted is
// the active one, so two hosts can never show two palettes. Only where no
// palette is mounted does the Falcon launcher keep the key.
const hosts = [];

/** Register a mounted palette; returns the function that removes it. */
export function registerPaletteHost(id) {
  hosts.push(id);
  return () => {
    const at = hosts.indexOf(id);
    if (at !== -1) hosts.splice(at, 1);
  };
}

/** The host that renders the palette and takes the key, or null. */
export const activePaletteHost = () => hosts[0] ?? null;

/** Whether the app palette owns this Cmd/Ctrl+K press (any target). */
export function appPaletteOwnsCommandShortcut() {
  return hosts.length > 0;
}

// The Falcon launcher asks under this name and leaves the key alone when true.
export const dsxOwnsCommandShortcut = appPaletteOwnsCommandShortcut;
