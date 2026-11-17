import React, { useState } from "react";
import ExternalRunHandoffDialog from "./ExternalRunHandoffDialog";
import { usePendingHandoffs } from "./externalRunHandoffStore";
import { canRerunHere } from "./runKind";

export function useExternalRunHandoff({ datasetId, disabled = false }) {
  const [action, setAction] = useState(null);
  const request = (run, scope, exampleIds) => {
    if (
      disabled ||
      run.task_kind !== "external" ||
      run.source === "http" ||
      canRerunHere(run)
    )
      return;
    const pending = usePendingHandoffs.getState().actions.get(datasetId);
    setAction(
      pending ?? {
        datasetId,
        runId: run.run_id,
        label: run.label ?? run.run_id,
        scope,
        exampleIds: [...exampleIds],
        newRunId: globalThis.crypto.randomUUID(),
      },
    );
  };
  return {
    request,
    dialog: action ? (
      <ExternalRunHandoffDialog
        key={action.newRunId}
        action={action}
        onClose={() => setAction(null)}
        disabled={disabled}
      />
    ) : null,
  };
}
