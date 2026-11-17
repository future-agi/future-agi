// The New experiment wizard's dataset version choice in a launch build: the
// server copies the dataset's current rows when it creates the experiment,
// or the rows of an earlier version picked on the Data step.
import { isSurfaceShown } from "src/launch/launchSwitch";
import { fDateTime } from "src/utils/format-time";

/** How many versions the wizard reads, newest first (one page). */
export const VERSION_LIST_LIMIT = 100;

/**
 * Whether the Data step offers the dataset's current rows as its version
 * choice: a launch build that shows dataset versions and reads their list.
 * Run's line for a version id follows the same answer.
 */
export const currentRowsChoiceShown = ({ runsCurrentRows, versionsHeld }) =>
  Boolean(runsCurrentRows) &&
  !versionsHeld &&
  isSurfaceShown("dataset.versions");

const examples = (n) =>
  `${Number(n).toLocaleString()} ${n === 1 ? "example" : "examples"}`;

// The server keeps a copy of the rows for every experiment it creates
// (native_run_binding.py, metadata.capture); other versions were saved.
export const isExperimentCopy = (v) =>
  v?.metadata?.capture === "native-snapshot-v1";

/**
 * A server version has no number: what it is (a copy kept for an
 * experiment, or a saved version). Pure.
 */
export const earlierVersionKind = (v) =>
  !v?.created_at
    ? "Earlier version"
    : isExperimentCopy(v)
      ? "Copied for an experiment"
      : "Saved version";

/** Its whole label: kind, when (with the year), note and size. Pure. */
export const earlierVersionText = (v) => {
  const when = v?.created_at
    ? fDateTime(v.created_at, "MMM d, yyyy, HH:mm")
    : "";
  return [
    earlierVersionKind(v),
    when || null,
    v?.description || null,
    v?.item_count != null || v?.row_count != null
      ? examples(v.item_count ?? v.row_count)
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
};
