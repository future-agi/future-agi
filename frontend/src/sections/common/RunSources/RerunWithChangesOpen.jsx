// GS-06 (options-truth L3 G10): the open `Rerun with changes…` dialog of
// RerunWithChangesHost, split off so the app shell does not load the matrix
// read, the estimates and the dialog's code blocks on every page. Loaded
// (React.lazy) when a rerun request names a run on the page.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { useMatrix } from "src/api/develop/r2";
import { scopeSets } from "src/sections/experiment-detail/iterate/iterateModel";
import { variantLetter } from "src/sections/common/Runs/runLabel";
import { rerunPlan } from "src/sections/experiment-detail/ExperimentBarRightSection/rerunPlan";
import RerunWithChangesDialog from "./RerunWithChangesDialog";

export default function RerunWithChangesOpen({
  run,
  datasetId,
  datasetKind,
  siblingsOf,
  request,
  close,
}) {
  const runsRes = { results: siblingsOf };
  // `Failed tries only`: rows with at least one failed try on this run,
  // counted from the Matrix exactly like R2's Iterate dock (scopeSets),
  // which is the set the server's `failed` scope reruns. (R3's `fails`
  // pill, a row mean under the threshold, said #2C = 4 rows / 12 tries
  // while the rerun made 9 / 27; integrator, R4 implementation.)
  const matrix = useMatrix(
    {
      datasetId,
      runIds: run ? [run.run_id] : [],
      baselineId: run?.run_id,
    },
    { enabled: Boolean(run) },
  );
  const failedRows = matrix.data
    ? scopeSets(matrix.data, run?.run_id).failed.length
    : undefined;
  // IT-R5 G6 P5: the Rerun menu's per-try numbers for this run (one
  // estimator for the menu and the dialog).
  const perTry = useMemo(
    () =>
      run && matrix.data
        ? rerunPlan(matrix.data, [run]).perRun[0]?.perTry ?? null
        : null,
    [run, matrix.data],
  );
  // L7c g5 (mock:connect-endpoints): connecting a machine is absent, and a
  // run that ran on one (a code run) offers no Rerun with changes (dataset integration
  // FE-0), so this dialog reads no machines list: no GET
  // /model-hub/r4/connect/machines/ from here.
  const nextLabel = useMemo(() => {
    if (!run) return null;
    const siblings = (runsRes?.results ?? []).filter(
      (r) => r.experiment_id === run.experiment_id,
    );
    // L7b G3: the next free letter after the experiment's highest one (a
    // kept run, #9A·2, shares its letter and takes none), as the rerun
    // names the child.
    const used = siblings
      .map((r) => r.letter ?? /^#\d+([A-Z])/.exec(r.label ?? "")?.[1])
      .filter(Boolean)
      .map((l) => l.charCodeAt(0) - 65);
    return `#${run.experiment_ordinal}${variantLetter(used.length ? Math.max(...used) + 1 : 0)}`;
  }, [run, runsRes]);
  return (
    <>
      <RerunWithChangesDialog
        open
        onClose={close}
        run={{ ...run, dataset_id: run.dataset_id ?? datasetId }}
        nextLabel={nextLabel}
        failedRows={failedRows}
        rowNoun={datasetKind === "task_suite" ? "task" : "row"}
        initialScope={request.scope ?? "all"}
        initialRowIds={(request.rowIds ?? []).map(
          (id) =>
            (matrix.data?.rows ?? []).find((r) => r.row_id === id)?.label ?? id,
        )}
        rowLabels={(matrix.data?.rows ?? []).map(
          (r) => r.label ?? `Row ${r.n}`,
        )}
        perTry={perTry}
      />
    </>
  );
}

RerunWithChangesOpen.propTypes = {
  run: PropTypes.object.isRequired,
  datasetId: PropTypes.string,
  datasetKind: PropTypes.string,
  siblingsOf: PropTypes.array,
  request: PropTypes.object.isRequired,
  close: PropTypes.func.isRequired,
};
