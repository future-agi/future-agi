import { create as createZustandStore } from "zustand";

import logger from "src/utils/logger";

// Every store made through this module, so logout can put each one back to
// its initial state. Stores hold user and workspace data (rows, traces, agent
// graphs, drafts), and a same-tab logout keeps the JavaScript modules loaded:
// without a reset the next account signed in on this tab would see it.
const stores = new Set();

const register = (store) => {
  stores.add(store);
  return store;
};

/**
 * Drop-in replacement for zustand's `create`. Use it for every store.
 * Supports both `create(initializer)` and the curried `create()(initializer)`.
 */
export const create = (initializer) =>
  initializer === undefined
    ? (curriedInitializer) => register(createZustandStore(curriedInitializer))
    : register(createZustandStore(initializer));

/**
 * Put every store back to the state it was created with, and remove what a
 * persisted store saved to browser storage. One failing store does not stop
 * the others.
 */
export const resetAllStores = () => {
  for (const store of stores) {
    try {
      // replace = true also removes keys that were added after creation.
      store.setState(store.getInitialState(), true);
      // persist() writes on setState; remove the entry so nothing remains.
      store.persist?.clearStorage?.();
    } catch (error) {
      logger.error("Failed to reset a store on logout", error);
    }
  }
};
