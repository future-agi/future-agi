// Who opens the Columns popover for a layout key. ColumnsMenu (priority 2)
// and ViewMenu with `columns` (priority 1) register as hosts while mounted;
// `openColumnsMenu(key)` (⌘K `Columns…`, the header menu's `Columns…`, the
// `C` key) asks the best host of that key to open. Module state, no React.

const hosts = new Map(); // key -> [{priority, open, token}]
let seq = 0;

/** Register a host; returns an unregister function. */
export function registerColumnsHost(key, open, priority = 1) {
  seq += 1;
  const token = seq;
  const list = hosts.get(key) ?? [];
  hosts.set(key, [...list, { priority, open, token }]);
  return () => {
    const next = (hosts.get(key) ?? []).filter((h) => h.token !== token);
    if (next.length) hosts.set(key, next);
    else hosts.delete(key);
  };
}

/**
 * Open the Columns popover of a layout key.
 * @param {string} key  `layout.key`
 * @param {{hiddenOnly?: boolean}} [opts]
 * @returns {boolean} false when no host is mounted
 */
export function openColumnsMenu(key, opts = {}) {
  const list = hosts.get(key) ?? [];
  if (!list.length) return false;
  const best = [...list].sort(
    (a, b) => b.priority - a.priority || b.token - a.token,
  )[0];
  best.open(opts);
  return true;
}

/** True when something can open the Columns popover of this key. */
export const hasColumnsHost = (key) => (hosts.get(key) ?? []).length > 0;
