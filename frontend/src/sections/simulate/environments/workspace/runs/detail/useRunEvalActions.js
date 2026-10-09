import { useState } from "react";
import { useRegradeEvals } from "./useRegradeEvals";

/**
 * The run page's one place to re-run evals, or edit one and then re-run it.
 *
 * The All Evaluations drawer and every eval column's ⋮ menu ask through here,
 * so they share one edit form, one confirm dialog and one request: a re-run
 * sent from either holds both until the server answers.
 *
 * `requestRerun(configs, { onSuccess })` opens the confirm on those evals.
 * `requestEdit(config, { onSuccess })` opens the edit form; saving it opens
 * the confirm on the edited eval, worded for a save that already happened.
 * Either `onSuccess` runs once the grading is queued. `dialogProps` go to `RunEvalDialogs`.
 */
export function useRunEvalActions({ envId, executionId }) {
  // The eval open for editing and who asked, or null.
  const [editing, setEditing] = useState(null);
  // The evals the confirm dialog is about and who asked, or null.
  const [confirming, setConfirming] = useState(null);
  // Whether the confirm follows a saved edit. Set only when it opens, so the
  // dialog keeps its wording while it fades out after closing.
  const [afterEdit, setAfterEdit] = useState(false);
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

  const requestRerun = (configs, { onSuccess } = {}) => {
    setAfterEdit(false);
    setConfirming({ configs, onSuccess });
  };
  const requestEdit = (config, { onSuccess } = {}) =>
    setEditing({ config, onSuccess });

  return {
    requestRerun,
    requestEdit,
    isPending,
    editOpen: Boolean(editing),
    dialogProps: {
      editing: editing?.config ?? null,
      confirming: confirming?.configs ?? null,
      afterEdit,
      loading: isPending,
      onEditClose: () => setEditing(null),
      onEdited: (updated) => {
        setEditing(null);
        if (updated) setAfterEdit(true);
        setConfirming(
          updated
            ? { configs: [updated], onSuccess: editing?.onSuccess }
            : null,
        );
      },
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
