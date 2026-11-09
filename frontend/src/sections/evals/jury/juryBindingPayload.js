import { isSurfaceShown } from "src/launch/launchSwitch";

// The composite picker carries its version in the binding's JSON contract as
// well as the generic pin, so older dataset writers retain the same selection.
export default function juryBindingPayload(payload, selection) {
  if (
    !isSurfaceShown("evals.jury") ||
    selection.templateType !== "composite" ||
    !selection.config?.jury ||
    !payload.config
  )
    return;
  payload.config.config = {
    ...payload.config.config,
    jury_version_id: selection.versionId || null,
  };
  // Older generic update contracts accept UUID pins only; the jury resolver
  // converts Latest into one immutable version before anything is persisted.
  if (selection.versionId === "latest") delete payload.pinned_version_id;
}
