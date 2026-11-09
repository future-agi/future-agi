import React, { useState } from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import { useSnackbar } from "notistack";
import { useNavigate } from "react-router-dom";

import { useDeleteSandbox, useSandboxMutation } from "../api/queries";
import SandboxIcon from "../components/SandboxIcon";
import { sandboxesPath } from "../lib/links";
import { sandboxErrorText } from "../lib/offerCopy";

/**
 * Hibernate / Resume and Delete for a sandbox the caller owns. Run sandboxes get
 * none. Delete leaves for the list before anything re-reads the sandbox. From
 * the confirm on it tells the page it is leaving (`onLeaving(true)`), so the
 * page never redirects on the way out, not even when its own poll reads
 * "destroyed" while the DELETE is in flight; a failed DELETE says so and
 * clears it (`onLeaving(false)`).
 */
export default function DetailActions({ sandbox, onLeaving }) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const [confirming, setConfirming] = useState(false);
  const notifyError = (error) =>
    enqueueSnackbar(sandboxErrorText(error), { variant: "error" });
  const hibernate = useSandboxMutation((api) => api.hibernate(sandbox.id), {
    onError: notifyError,
  });
  const resume = useSandboxMutation((api) => api.resume(sandbox.id), {
    onError: notifyError,
  });
  const destroy = useDeleteSandbox(sandbox.id, {
    onError: (error) => {
      onLeaving?.(false);
      notifyError(error);
    },
    onDeleted: () => {
      // Replace: Back must not land on the deleted sandbox's tab.
      navigate(sandboxesPath(), { replace: true });
      enqueueSnackbar(`Deleted ${sandbox.name}`, { variant: "success" });
    },
  });

  if (sandbox.readOnly || sandbox.state === "destroyed") return null;
  const busy = hibernate.isPending || resume.isPending || destroy.isPending;

  return (
    <>
      {sandbox.state === "active" && (
        <LoadingButton
          size="small"
          variant="outlined"
          color="inherit"
          loading={hibernate.isPending}
          disabled={busy}
          onClick={() => hibernate.mutate()}
          startIcon={<SandboxIcon name="pause" size={14} />}
          data-testid="sandbox-hibernate"
        >
          Hibernate
        </LoadingButton>
      )}
      {sandbox.state === "hibernated" && (
        <LoadingButton
          size="small"
          variant="contained"
          color="primary"
          loading={resume.isPending}
          disabled={busy}
          onClick={() => resume.mutate()}
          startIcon={<SandboxIcon name="play" size={14} />}
          data-testid="sandbox-resume"
        >
          Resume
        </LoadingButton>
      )}
      <Button
        size="small"
        color="inherit"
        disabled={busy}
        onClick={() => setConfirming(true)}
        startIcon={<SandboxIcon name="trash" size={14} />}
        data-testid="sandbox-delete"
        sx={{ color: "text.secondary" }}
      >
        Delete
      </Button>
      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ typography: "m3" }}>
          Delete {sandbox.name}?
        </DialogTitle>
        <DialogContent>
          <Typography typography="s1" color="text.secondary">
            Its container stops and its files are removed. Restore a snapshot as
            a new sandbox first if you need its files later.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
          <LoadingButton
            color="error"
            variant="contained"
            loading={destroy.isPending}
            onClick={() => {
              onLeaving?.(true);
              destroy.mutate();
            }}
            data-testid="sandbox-delete-confirm"
          >
            Delete
          </LoadingButton>
        </DialogActions>
      </Dialog>
    </>
  );
}

DetailActions.propTypes = {
  sandbox: PropTypes.object.isRequired,
  onLeaving: PropTypes.func,
};
