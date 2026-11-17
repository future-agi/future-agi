import React, { useState } from "react";
import ConfirmDialog from "../components/ConfirmDialog";

// Draft removals name the object before editing the saved configuration's
// copy, and name the button that applies the draft (applyLabel): Save &
// Activate in the config editors, Save & Apply on the Fallbacks page.
/** @returns {[(name: string, action: () => void) => void, React.ReactElement]} */
export function useConfirmRemoval(applyLabel = "Save & Activate") {
  const [pending, setPending] = useState(null);
  const requestRemoval = (name, action) => {
    setPending({ name, action });
  };
  const confirmation = (
    <ConfirmDialog
      open={Boolean(pending)}
      title="Remove configuration"
      message={`Remove "${pending?.name}" from the draft? Click ${applyLabel} to apply the removal.`}
      confirmLabel="Remove"
      onClose={() => setPending(null)}
      onConfirm={() => {
        pending?.action();
        setPending(null);
      }}
    />
  );
  return [requestRemoval, confirmation];
}
