import { create } from "zustand";

/**
 * Confirm step of the run actions (useRunActions). A host component
 * (RunActionConfirmHost, mounted with the experiment drawer on the dataset
 * and experiment pages) renders the dialog; where no host is mounted the
 * action runs straight away and only toasts. With several hosts mounted,
 * the first one renders.
 */
let nextToken = 1;
export const useRunActionConfirmStore = create((set) => ({
  hosts: [],
  request: null,
  mount: () => {
    const token = nextToken;
    nextToken += 1;
    set((s) => ({ hosts: [...s.hosts, token] }));
    return token;
  },
  unmount: (token) =>
    set((s) => ({ hosts: s.hosts.filter((t) => t !== token) })),
  open: (request) => set({ request }),
  close: () => set({ request: null }),
}));

/**
 * @param {{title: string, content: string, confirmLabel: string, danger?: boolean}} req
 * @returns {Promise<boolean>} true to go ahead
 */
export function confirmRunAction(req) {
  const store = useRunActionConfirmStore.getState();
  if (!store.hosts.length) return Promise.resolve(true);
  return new Promise((resolve) => store.open({ ...req, resolve }));
}
