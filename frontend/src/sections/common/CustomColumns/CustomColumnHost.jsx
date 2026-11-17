import React, { useEffect, useMemo } from "react";
import PropTypes from "prop-types";
import { useRunResults } from "src/api/develop/run-results";
import CustomColumnDialog from "./CustomColumnDialog";
import {
  customColumnsAvailable,
  customColumnsOffered,
} from "./useCustomColumns";
import {
  closeCustomColumnDoor,
  registerCustomColumnHost,
  useCustomColumnDoor,
} from "./customColumnDoor";

/**
 * HO results R1 (A-H4): the results page's `Add a column` / `Edit column`
 * dialog, opened from any column ⋮ menu through customColumnDoor. It reads
 * the page's own R3 view (a cache hit), so the preview and the path
 * suggestions come from the rows in view.
 * Props: datasetId, view (the page's R3 view), runs (run chips: run_id, label).
 */
const CustomColumnHost = ({ datasetId, view, runs = [] }) => {
  const door = useCustomColumnDoor();
  const available = customColumnsAvailable() && Boolean(datasetId);
  // No route for custom columns in this build: the dialog never opens.
  const open = door.open && customColumnsOffered();
  useEffect(
    () => (available ? registerCustomColumnHost() : undefined),
    [available],
  );
  const { data } = useRunResults(datasetId, view, {
    enabled: available && open && !!view?.runIds?.length,
  });
  const runLabels = useMemo(
    () => Object.fromEntries(runs.map((r) => [r.run_id ?? r.id, r.label])),
    [runs],
  );
  const outputs = useMemo(
    () =>
      (data?.rows ?? [])
        .flatMap((r) =>
          Object.values(r.results ?? {}).map((x) => x?.output ?? null),
        )
        .filter((o) => o !== null && o !== ""),
    [data],
  );
  if (!available) return null;
  return (
    <CustomColumnDialog
      open={open}
      onClose={closeCustomColumnDoor}
      datasetId={datasetId}
      view={view}
      runLabels={runLabels}
      outputs={outputs}
      initial={door.initial}
    />
  );
};

CustomColumnHost.propTypes = {
  datasetId: PropTypes.string,
  view: PropTypes.object,
  runs: PropTypes.array,
};

export default CustomColumnHost;
