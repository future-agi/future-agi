import { useMemo } from "react";
import { pairwiseOptions } from "src/sections/develop-detail/Review/reviewModel";
import { isTextInput } from "src/utils/keyboard-guards";

// The pairwise vote of the result drawer. Saving
// reviews isn't available in this release: neither the Review tab nor the
// Compare tab sends a vote, and the Review tab reads the session's picks
// from the map below.

/** The viewer's own pairwise votes this session, by `<A>|<B>|<example>`. */
export const myPairVotes = new Map();
export const pairVoteKey = (A, B, exampleId) => `${A}|${B}|${exampleId}`;

// Digits never fire inside text fields or open lists (menus, listboxes,
// sliders), and never while a dialog or popover is open.
export const voteKeysBlocked = (el) =>
  isTextInput(el) ||
  Boolean(el?.closest?.('[role="listbox"], [role="menu"], [role="slider"]')) ||
  Boolean(
    typeof document !== "undefined" &&
      document.querySelector(
        ".MuiDialog-root, .MuiPopover-root, .MuiMenu-root",
      ),
  );

/**
 * The Compare tab's vote: baseline (A) vs the focused run (B), shown as
 * baseline better / tie / candidate better. Saving reviews isn't available
 * in this release, so the votes send nothing and keys 1 / 2 / 3 do nothing.
 * Returns `{active, options, labelA, labelB}`; `active` is false with fewer
 * than two runs.
 */
export function usePairVote({ exampleId, runs, baselineId, focusedRunId }) {
  const base = (runs ?? []).find((r) => r.run_id === baselineId) ?? runs?.[0];
  const focused =
    (runs ?? []).find(
      (r) => r.run_id === focusedRunId && r.run_id !== base?.run_id,
    ) ?? (runs ?? []).find((r) => r.run_id !== base?.run_id);
  const A = base?.run_id ?? null;
  const B = focused?.run_id ?? null;
  const active = Boolean(A && B && exampleId);
  const options = useMemo(
    () =>
      pairwiseOptions({
        left: "A",
        labelA: base?.label,
        labelB: focused?.label,
      }),
    [base?.label, focused?.label],
  );
  return {
    active,
    options,
    labelA: base?.label,
    labelB: focused?.label,
  };
}
