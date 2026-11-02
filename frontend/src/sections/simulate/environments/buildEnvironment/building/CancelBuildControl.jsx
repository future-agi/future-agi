import PropTypes from "prop-types";
import { useState } from "react";
import { Button, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useCancelHarnessJob } from "src/api/simulate-environments/cancelBuild";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import ViewerLock from "../../components/ViewerLock";
import { WORKSPACE_COPY } from "../../workspace/workspace.constants";

/**
 * Cancel an in-progress environment build, from the build pane.
 *
 * A build can only be cancelled while it is actually building; once it has
 * failed or finished there is nothing to stop. `building` carries that gate
 * (running && !failure) from BuildingPane, so this renders nothing otherwise —
 * the whole reason the control isn't always on screen.
 *
 * The confirm's dismiss button reads "Cancel" (ConfirmDialog's fixed label), so
 * the destructive action says "Stop build" to keep the two apart.
 */
export default function CancelBuildControl({ envId, building = false }) {
  const [confirming, setConfirming] = useState(false);
  const cancel = useCancelHarnessJob(envId);
  const { canEdit } = useSimulationAccess();

  if (!building || !envId) return null;

  const onConfirm = () => {
    setConfirming(false);
    cancel.mutate(undefined, {
      onSuccess: () => enqueueSnackbar("Build canceled", { variant: "success" }),
      // A cancel that fails quietly reads as a dead button, so surface it.
      onError: () =>
        enqueueSnackbar("Couldn't cancel the build. Try again", { variant: "error" }),
    });
  };

  return (
    <>
      <ViewerLock locked={!canEdit} title={WORKSPACE_COPY.viewer.cancel}>
        <Button
          size="small"
          variant="outlined"
          color="error"
          disabled={!canEdit || cancel.isPending}
          onClick={() => setConfirming(true)}
          startIcon={<Iconify icon="solar:close-circle-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
        >
          {cancel.isPending ? "Canceling…" : "Cancel build"}
        </Button>
      </ViewerLock>
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title="Stop this build?"
        content={(
          <Typography component="span" sx={{ typography: "s2" }}>
            This build will be cancelled and the environment won&apos;t be created. You can start
            a new build afterwards.
          </Typography>
        )}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={onConfirm}
            sx={{ paddingX: "24px" }}
          >
            Stop build
          </Button>
        }
      />
    </>
  );
}

CancelBuildControl.propTypes = {
  envId: PropTypes.string,
  building: PropTypes.bool,
};
