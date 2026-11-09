import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Popover, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import useRunActions from "src/sections/common/Runs/useRunActions";
import { runProgressOf } from "src/sections/common/Runs/runStatus";
import ExternalRunSnippet from "src/sections/develop-detail/Experiment/ExternalRunSnippet";
import { waitingPhraseOf } from "src/sections/common/Runs/runLedger";

const capFirst = (t) => t.charAt(0).toUpperCase() + t.slice(1);

/**
 * An external run waiting for results (J7): its state, and in prototype mode
 * the labelled `Simulate the upload` (options-truth L5 G5: no upload command
 * is printed while the upload route is not real).
 */
const UploadResultsPopover = ({ run, anchorEl, onClose }) => {
  const actions = useRunActions(run);
  const prog = runProgressOf(run);
  const waiting = run.status === "WaitingForResults";
  return (
    <Popover
      open={!!anchorEl}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { width: 520, p: 2, borderRadius: 1 } } }}
    >
      <Stack spacing={1.25}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
        >
          <Box>
            <Typography typography="m3" fontWeight="fontWeightSemiBold">
              Upload results for {run.label}
            </Typography>
            <Typography typography="s2" color="text.secondary">
              {waiting
                ? `${capFirst(waitingPhraseOf(run))} · ${prog.done}/${prog.total} rows uploaded`
                : `${prog.done}/${prog.total} rows uploaded`}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
        {/* L5 G5: only the labelled Simulate the upload. */}
        <ExternalRunSnippet
          experimentId={run.experiment_id}
          runId={run.run_id}
          runLabel={run.label}
          trials={run.trials ?? 1}
          dense
          simulating={actions.pending}
          onSimulateUpload={
            waiting
              ? async () => {
                  await actions.simulateUpload();
                  onClose();
                }
              : undefined
          }
        />
      </Stack>
    </Popover>
  );
};

UploadResultsPopover.propTypes = {
  run: PropTypes.object.isRequired,
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
};

export default UploadResultsPopover;
