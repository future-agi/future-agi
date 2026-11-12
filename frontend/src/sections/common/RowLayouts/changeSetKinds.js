// Row layouts (W9-4, B3): which button kinds this branch can run, read from
// the change-set module itself. `set_status` / `set_tags` and the row status
// labels arrive with W3 A (changeSetStore OP_TYPES, ROW_STATUS_LABEL); until
// then those kinds stay absent. A namespace import, because the labels are
// not exported before W3 A.
import * as changeSet from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { controlKinds } from "./controlOps";

/** The row status labels (`{approved: "Approved", …}`), or null. */
export function rowStatusLabels(mod = changeSet) {
  const labels = mod.ROW_STATUS_LABEL;
  return labels && typeof labels === "object" && Object.keys(labels).length
    ? labels
    : null;
}

/**
 * The kinds the change-set applies and the Customize panel can edit: a
 * status button picks from the row status labels, so it needs them.
 */
export function availableControlKinds(mod = changeSet) {
  const statuses = rowStatusLabels(mod);
  return controlKinds(mod.OP_TYPES ?? []).filter(
    (k) => k !== "set_status" || Boolean(statuses),
  );
}
