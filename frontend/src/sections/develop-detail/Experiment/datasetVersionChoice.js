// The New experiment wizard's dataset version choice in a launch build: the
// server copies the dataset's current rows when it creates the experiment,
// and an earlier version can't be run yet.
import { isSurfaceShown } from "src/launch/launchSwitch";

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
