import { create } from "src/utils/zustand";

// Retain unconfirmed creates across close/remount; the shared store resets on logout.
// Never persist inputs/configuration or authentication in browser storage.
export const usePendingHandoffs = create(() => ({ actions: new Map() }));
export const hold = (datasetId, action) =>
  usePendingHandoffs.setState(({ actions }) => {
    const next = new Map(actions);
    if (action) next.set(datasetId, action);
    else next.delete(datasetId);
    return { actions: next };
  });
