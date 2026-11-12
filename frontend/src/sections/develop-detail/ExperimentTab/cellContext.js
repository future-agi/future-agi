// Live view state for the leaderboard's cell renderers. AG Grid reads its
// own `context` option once, so the grid also provides this React context
// (cell renderers render in portals inside the grid's React tree): a poll or
// a view-setting change re-renders cells in place instead of remounting
// them, which keeps hover cards and menus open.
import { createContext, useContext } from "react";

export const LeaderboardContext = createContext(null);

/** `{node, data, context}` for a cell; `data` is the node's latest data. */
export function useCell(params) {
  const live = useContext(LeaderboardContext);
  return {
    node: params.node,
    data: params.node?.data ?? params.data,
    context: live ?? params.context,
  };
}

/**
 * EL#12: the baseline run's natural row while its pinned copy sits on top.
 * It renders as a ghost (`#2B · pinned above`): no numbers, no actions, not
 * selectable, so the baseline appears once.
 */
export function isGhostBaseline(node, data, context) {
  return (
    !!data &&
    !node?.group &&
    !node?.rowPinned &&
    !!context?.baselineRun &&
    data.run_id === context.baselineRun.run_id
  );
}
