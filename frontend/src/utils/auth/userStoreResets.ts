type UserStoreName = "agent-playground" | "graph-builder";

const resets = new Map<UserStoreName, () => void>();

// Lazy stores register on module load; an unloaded store has no state to clear.
export const registerUserStoreReset = (
  name: UserStoreName,
  reset: () => void,
) => {
  resets.set(name, reset);
};

// Keep custom cleanup synchronous and in clearStores' existing reset order.
export const resetUserStore = (name: UserStoreName) => {
  resets.get(name)?.();
};
