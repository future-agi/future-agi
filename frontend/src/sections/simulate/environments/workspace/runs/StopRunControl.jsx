import PropTypes from "prop-types";
import { useState } from "react";
import { Button, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useCancelExecution } from "src/sections/common/simulation/hooks/useCancelExecution";

/**
 * Stop a live simulation run, from the runs table or the run-detail header.
 *
 * Renders nothing unless the run can still be stopped. On confirm it POSTs the
 * shared cancel endpoint (useCancelExecution → test-executions/{id}/cancel/,
 * the same one the product test-run pages use) and refreshes the runs list and
 * this run's detail so the status moves on the next read.
 *
 * Sized to the StatusChip it sits beside (22px). `label` is "Stop" in the tight
 * table cell; the run-detail header has room for "Stop simulation".
 */
export default function StopRunControl({ executionId, stoppable = false, label = "Stop" }) {
  // Gate before any hook so a finished run (most rows) costs nothing.
  if (!stoppable || !executionId) return null;
  return <StopRunButton executionId={executionId} label={label} />;
}

StopRunControl.propTypes = {
  executionId: PropTypes.string,
  stoppable: PropTypes.bool,
  label: PropTypes.string,
};

function StopRunButton({ executionId, label }) {
  const [confirming, setConfirming] = useState(false);
  const queryClient = useQueryClient();
  const cancel = useCancelExecution();

  const onConfirm = () => {
    setConfirming(false);
    cancel.mutate(executionId, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["run-test-executions"] });
        queryClient.invalidateQueries({ queryKey: ["simulation-run-results-v3", executionId] });
        enqueueSnackbar("Cancelling the run", { variant: "success" });
      },
      onError: () => enqueueSnackbar("Couldn't stop the run — try again", { variant: "error" }),
    });
  };

  return (
    // The dialog portals out of the DOM but its React events still bubble here,
    // so stop them at the root — a click inside must never open the table row.
    <span onClick={(e) => e.stopPropagation()} role="presentation" style={{ display: "inline-flex" }}>
      {/* Shaped like the product test-runs grid's Stop (TestRunsGrid StatusCell),
          at the StatusChip's height, in red since it cancels the run. */}
      <Button
        variant="outlined"
        color="error"
        size="small"
        aria-label="Stop simulation"
        disabled={cancel.isPending}
        onClick={() => setConfirming(true)}
        startIcon={<Iconify icon="bi:stop-circle" width={12} />}
        sx={{
          flexShrink: 0,
          whiteSpace: "nowrap",
          borderRadius: "4px",
          px: 1,
          minWidth: 0,
          height: 22,
          textTransform: "none",
          fontSize: 12,
          "& .MuiButton-startIcon": { mr: 0.5 },
        }}
      >
        {cancel.isPending ? "Stopping…" : label}
      </Button>
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title="Stop this run?"
        content={(
          <Typography component="span" sx={{ typography: "s2" }}>
            The simulation will be cancelled and won&apos;t finish. Results it has
            already produced are kept.
          </Typography>
        )}
        action={(
          <Button size="small" variant="contained" color="error" onClick={onConfirm} sx={{ px: 3 }}>
            Stop run
          </Button>
        )}
      />
    </span>
  );
}

StopRunButton.propTypes = {
  executionId: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
};
