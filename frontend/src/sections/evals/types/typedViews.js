// W7 A0: the typed eval types' Evals views (create editor, Details), in one
// map, so a mount shows a type only when its view is real (`ready`).
import DagEvalDetail from "./DagEvalDetail";
import DagEvalEditor from "./DagEvalEditor";
import RemoteScorerDetail from "./RemoteScorerDetail";
import RemoteScorerEditor from "./RemoteScorerEditor";
import { isSurfaceShown } from "src/launch/launchSwitch";

export const TYPED_EDITORS = { dag: DagEvalEditor, remote: RemoteScorerEditor };
export const TYPED_DETAILS = { dag: DagEvalDetail, remote: RemoteScorerDetail };

// L6 launch switch: the W7 types (Decision tree, Remote scorer) call
// endpoints dev does not have, so a launch build shows neither.
const TYPED_SURFACE = { dag: "evals.dag", remote: "evals.remote" };
const typedShown = (type) =>
  TYPED_SURFACE[type] ? isSurfaceShown(TYPED_SURFACE[type]) : false;

/** Direct mounts and tabs enforce the same typed-surface decision. */
export const hasTypedEditor = (evalType) =>
  Boolean(TYPED_EDITORS[evalType]?.ready) && typedShown(evalType);

/** The typed type tabs whose editor is real (an absent one is not shown). */
export const typedTabsOf = (types) =>
  types.filter((t) => hasTypedEditor(t.value));

/** Whether a typed eval's Details view is real. */
export const hasTypedDetail = (evalType) =>
  Boolean(TYPED_DETAILS[evalType]?.ready) && typedShown(evalType);
