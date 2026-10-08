import { useState } from "react";
import { useRegradeEvals } from "./useRegradeEvals";

/**
 * The run page's one place to re-run evals or edit one.
 *
 * The All Evaluations drawer and every eval column's ⋮ menu ask through here,
 * so they share one edit form, one confirm dialog and one request: a re-run
 * sent from either holds both until the server answers.
 *
 * `requestRerun(configs, { onSuccess })` opens the confirm on those evals;
 * `onSuccess` runs once the grading is queued. `requestEdit(config)` opens
 * the edit form, and saving it only saves: grading the run again with the
 * edited eval is a separate choice, made through `requestRerun`.
 * `dialogProps` go to `RunEvalDialogs`.
 */
export function useRunEvalActions({ envId, executionId }) {
  // The eval open for editing, or null.
  const [editing, setEditing] = useState(null);
  // The evals the confirm dialog is about and who asked, or null.
  const [confirming, setConfirming] = useState(null);
  // Both dialogs are about one run. They close in the render that first sees
  // another run, rather than in an effect, so the old run's dialog is never
  // painted over the new one.
  const [shownFor, setShownFor] = useState(executionId);
  if (shownFor !== executionId) {
    setShownFor(executionId);
    setEditing(null);
    setConfirming(null);
  }
  const { regrade, isPending } = useRegradeEvals({ envId, executionId });

  const requestRerun = (configs, { onSuccess } = {}) =>
    setConfirming({ configs, onSuccess });
  const requestEdit = (config) => setEditing(config);

  return {
    requestRerun,
    requestEdit,
    isPending,
    editOpen: Boolean(editing),
    dialogProps: {
      editing,
      confirming: confirming?.configs ?? null,
      loading: isPending,
      onEditClose: () => setEditing(null),
      onEdited: () => setEditing(null),
      onConfirmClose: () => setConfirming(null),
      onConfirm: (list) =>
        regrade(list, {
          onSuccess: () => {
            setConfirming(null);
            confirming?.onSuccess?.();
          },
        }),
    },
  };
}
