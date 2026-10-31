import PropTypes from "prop-types";
import { useState } from "react";
import { Button, IconButton, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { ConfirmDialog } from "src/components/custom-dialog";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import { isFinishedRun } from "./summaryData";

const runsText = (n) => `${n} ${n === 1 ? "run" : "runs"}`;

// What a selection of runs can do, in place of the runs bar's own actions.
// One finished run is a baseline; two or more finished runs are a
// comparison. A run still going has no results, so it is neither. Deleting
// and the baseline have no API yet: Delete confirms and saves nothing, and
// the baseline is kept in this browser only.
export default function RunsSelectionBar({
  selected,
  baselineId,
  onBaseline,
  onCompare,
  onClear,
}) {
  const [deleting, setDeleting] = useState(false);
  const finished = selected.filter(isFinishedRun);
  const allFinished = finished.length === selected.length;
  const one = selected.length === 1;
  const isBaseline = one && selected[0].id === baselineId;

  const confirmDelete = () => {
    setDeleting(false);
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
    onClear();
  };

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ flexShrink: 0 }}
    >
      <Typography sx={{ typography: "s2", color: "text.secondary", mr: 0.5 }}>
        {selected.length} selected
      </Typography>

      {one ? (
        allFinished && (
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() => onBaseline(isBaseline ? null : selected[0].id)}
              startIcon={
                <Iconify icon="solar:transfer-vertical-linear" width={16} />
              }
            >
              {isBaseline ? "Clear baseline" : "Set as baseline"}
            </Button>
            <UiOnlyFlag feature="runBaseline" />
          </Stack>
        )
      ) : (
        <CustomTooltip
          show={!allFinished}
          arrow
          size="small"
          title="Only finished runs can be compared"
        >
          <span>
            <Button
              variant="contained"
              color="primary"
              size="small"
              disabled={!allFinished}
              onClick={onCompare}
              startIcon={
                <Iconify icon="solar:transfer-horizontal-linear" width={16} />
              }
            >
              Compare
            </Button>
          </span>
        </CustomTooltip>
      )}

      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Button
          variant="outlined"
          color="inherit"
          size="small"
          onClick={() => setDeleting(true)}
          startIcon={<Iconify icon="solar:trash-bin-trash-linear" width={16} />}
          sx={{ borderColor: "divider" }}
        >
          Delete
        </Button>
        <UiOnlyFlag feature="deleteRun" />
      </Stack>

      <CustomTooltip show arrow size="small" title="Clear selection">
        <IconButton size="small" aria-label="Clear selection" onClick={onClear}>
          <Iconify
            icon="solar:close-linear"
            width={16}
            sx={{ color: "text.subtitle" }}
          />
        </IconButton>
      </CustomTooltip>

      <ConfirmDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        title={`Delete ${runsText(selected.length)}?`}
        content="Their results go with them, and a baseline measured against them is cleared. The scenarios and the environment are untouched."
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={confirmDelete}
          >
            Delete
          </Button>
        }
      />
    </Stack>
  );
}

RunsSelectionBar.propTypes = {
  // The ticked summary rows.
  selected: PropTypes.arrayOf(PropTypes.object).isRequired,
  baselineId: PropTypes.string,
  // Called with the run id to set, or null to clear.
  onBaseline: PropTypes.func.isRequired,
  onCompare: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
};
