// R2 (L2) · `Iterate on #2B from here…` in the result drawer's Trajectory
// footer (R2-SPEC §4.2): closes the drawer and opens the dock on this run
// with scope `This row` (a quick test; the user can widen the scope).
import React from "react";
import PropTypes from "prop-types";
import { Button, Stack } from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { iterateHref } from "src/sections/common/Analysis/analysisLinks";
import { runLabel } from "src/sections/common/Runs/runLabel";
import { useRunConfigs } from "src/api/develop/r2";
import { useExperimentDetail } from "src/api/develop/runs";
import { useParams } from "react-router";
import { VIEWER_TIP } from "./IterateButton";
import { canIterate } from "./iterateModel";

// Drawer params dropped so the dock is not hidden behind the drawer.
const DRAWER_PARAMS = ["row", "trial", "dtab", "compareTraces"];

/**
 * The footer link (pure; exported for tests). R2D-01: with an R5 cause
 * whose rows include this row, the door opens on `These 6 rows` (the
 * cause's rows) with its change preset; R2D-15: `node` preselects the
 * first wrong step's node in the dock.
 */
export function fromHereHref(
  pathname,
  search,
  { runId, rowId, cause, nodeId, thisRowOnly = false },
) {
  const params = new URLSearchParams(search);
  DRAWER_PARAMS.forEach((k) => params.delete(k));
  const causeRows = (cause?.rows ?? []).map((r) => r.row_label ?? r.row_id);
  const useCause = !thisRowOnly && causeRows.length > 1;
  const href = `${pathname}${iterateHref(`?${params.toString()}`, {
    runId,
    scope: useCause ? "rows" : rowId ? "row" : "all",
    rows: useCause ? causeRows : rowId ? [rowId] : undefined,
    change: useCause ? cause.id : undefined,
    cause: useCause ? cause.id : undefined,
  })}`;
  return nodeId ? `${href}&node=${encodeURIComponent(nodeId)}` : href;
}

const IterateFromHereButton = ({ runId, rowId, cause, nodeId }) => {
  const { pathname, search } = useLocation();
  const { dataset, experimentId } = useParams();
  const { data: experiment } = useExperimentDetail(experimentId, {
    enabled: !dataset && Boolean(experimentId),
  });
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const datasetId = dataset ?? experiment?.dataset_id ?? null;
  const configs = useRunConfigs(datasetId, { enabled: Boolean(runId) });
  const run = (configs.data?.results ?? []).find((r) => r.run_id === runId);
  // dataset integration FE-0: no door for a run Test a fix does not apply to.
  if (!runId || (run && !canIterate(run))) return null;
  const label = run ? runLabel(run) : null;
  const causeRows = cause?.rows?.length ?? 0;
  return (
    <Stack direction="row" justifyContent="flex-end" sx={{ pt: 0.5 }}>
      <CustomTooltip
        show
        title={
          !canEdit
            ? VIEWER_TIP
            : causeRows > 1
              ? `Opens Test a fix on the ${causeRows} rows that fail the same way (${cause.title}), with ${nodeId ?? "the first wrong step"} selected.`
              : "Change this run's config, rerun this row as a quick test, and compare."
        }
        size="small"
        arrow
      >
        <span>
          <Button
            size="small"
            variant="outlined"
            color="primary"
            disabled={!canEdit}
            data-testid="iterate-from-here"
            startIcon={<Iconify icon="mdi:source-branch" width={16} />}
            onClick={() =>
              navigate(
                fromHereHref(pathname, search, {
                  runId,
                  rowId,
                  cause,
                  nodeId,
                }),
              )
            }
          >
            {label
              ? `Test a fix on ${label} from here…`
              : "Test a fix from here…"}
          </Button>
        </span>
      </CustomTooltip>
      {causeRows > 1 && canEdit && (
        <Button
          size="small"
          color="inherit"
          sx={{ ml: 0.5, typography: "s2", color: "text.secondary" }}
          data-testid="iterate-this-row"
          onClick={() =>
            navigate(
              fromHereHref(pathname, search, {
                runId,
                rowId,
                nodeId,
                thisRowOnly: true,
              }),
            )
          }
        >
          This row only
        </Button>
      )}
    </Stack>
  );
};

IterateFromHereButton.propTypes = {
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  runId: PropTypes.string,
  baselineId: PropTypes.string,
  trial: PropTypes.number,
  rowId: PropTypes.string,
  nodeId: PropTypes.string,
  cause: PropTypes.object,
};

export default IterateFromHereButton;
