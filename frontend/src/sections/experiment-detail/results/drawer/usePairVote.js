import { useCallback, useEffect, useMemo, useState } from "react";
import { useAnnotationLabelsList } from "src/api/annotation-labels/annotation-labels";
import { useSubmitResultReview } from "src/api/develop/review";
import {
  choiceText,
  choiceValue,
  pairwiseOptions,
} from "src/sections/develop-detail/Review/reviewModel";
import { isTextInput } from "src/utils/keyboard-guards";
import { pairOf, useReviewMarks } from "../reviewMarks";

// HO results R5 (W4-3): one pairwise vote for the result drawer. The Review
// tab and the Compare tab save through the same send (V2 result-reviews,
// mode pairwise) and read the same session votes, so a vote cast on one
// tab shows on the other and on the grid's row mark.

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
 * The V2 pairwise save (`choice` null clears the vote). Extracted from the
 * Review tab; `onSaved(exId)` / `onFailed(exId)` are the caller's.
 */
export function usePairSend({
  datasetId,
  A,
  B,
  exampleId,
  pairLabel,
  blind = false,
  left = "A",
  submit,
  onSaved,
  onFailed,
}) {
  return useCallback(
    (choice, { notes, exId = exampleId } = {}) => {
      if (!exId || !A || !B || !submit) return;
      submit.mutate(
        {
          example_id: exId,
          mode: "pairwise",
          run_a: A,
          run_b: B,
          values: [
            {
              ...(pairLabel?.id ? { label_id: pairLabel.id } : {}),
              value: choiceValue(choice),
            },
          ],
          notes: notes ?? "",
          blind: Boolean(blind),
          left,
        },
        {
          onSuccess: () => onSaved?.(exId),
          onError: () => {
            myPairVotes.delete(pairVoteKey(A, B, exId));
            onFailed?.(exId);
          },
        },
      );
    },
    // datasetId keys the submit hook the caller passes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      datasetId,
      exampleId,
      A,
      B,
      pairLabel,
      blind,
      left,
      submit,
      onSaved,
      onFailed,
    ],
  );
}

/**
 * The Compare tab's vote: baseline (A) vs the focused run (B), keys 1 / 2 /
 * 3 = A better / tie / B better (the Review tab's keys, unblinded). J / K
 * keep moving rows. Returns `{active, options, myChoice, vote, saved,
 * text}`; `active` is false with fewer than two runs.
 */
export function usePairVote({
  datasetId,
  exampleId,
  runs,
  baselineId,
  focusedRunId,
  keys = true,
}) {
  const base = (runs ?? []).find((r) => r.run_id === baselineId) ?? runs?.[0];
  const focused =
    (runs ?? []).find(
      (r) => r.run_id === focusedRunId && r.run_id !== base?.run_id,
    ) ?? (runs ?? []).find((r) => r.run_id !== base?.run_id);
  const A = base?.run_id ?? null;
  const B = focused?.run_id ?? null;
  const active = Boolean(A && B && exampleId);
  const labelsQ = useAnnotationLabelsList({ search: "", limit: 100 });
  const pairLabel = (labelsQ.data?.results ?? []).find(
    (l) => !l.archived && l.settings?.kind === "pairwise",
  );
  const submit = useSubmitResultReview(datasetId);
  const [version, setVersion] = useState(0);
  const [savedKey, setSavedKey] = useState(null);
  const key = pairVoteKey(A, B, exampleId);
  const sendPair = usePairSend({
    datasetId,
    A,
    B,
    exampleId,
    pairLabel,
    submit,
    onSaved: useCallback(
      (exId) => setSavedKey(pairVoteKey(A, B, exId)),
      [A, B],
    ),
    onFailed: useCallback(() => setVersion((v) => v + 1), []),
  });
  const options = useMemo(
    () =>
      pairwiseOptions({
        left: "A",
        labelA: base?.label,
        labelB: focused?.label,
      }),
    [base?.label, focused?.label],
  );
  void version;
  const myChoice = myPairVotes.get(key)?.choice ?? null;
  const vote = useCallback(
    (choice) => {
      if (!active) return;
      const same = myPairVotes.get(key)?.choice === choice;
      const next = same ? null : choice;
      if (next)
        myPairVotes.set(key, { choice: next, notes: "", at: Date.now() });
      else myPairVotes.delete(key);
      useReviewMarks.getState().mark(exampleId, next, false, pairOf(A, B));
      setVersion((v) => v + 1);
      setSavedKey(null);
      sendPair(next, { notes: "" });
    },
    [active, key, exampleId, sendPair, A, B],
  );
  useEffect(() => {
    if (!keys || !active) return undefined;
    const onKey = (e) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (voteKeysBlocked(document.activeElement)) return;
      const opt = options.find((o) => o.key === e.key);
      if (!opt) return;
      e.preventDefault();
      vote(opt.choice);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [keys, active, options, vote]);
  return {
    active,
    options,
    myChoice,
    vote,
    saved: savedKey === key && Boolean(myChoice),
    saving: submit.isPending,
    text: myChoice
      ? choiceText(myChoice, { labelA: base?.label, labelB: focused?.label })
      : "",
    labelA: base?.label,
    labelB: focused?.label,
  };
}
