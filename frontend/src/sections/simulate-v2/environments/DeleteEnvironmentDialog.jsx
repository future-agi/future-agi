import PropTypes from "prop-types";
import {
  Button,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
} from "@mui/material";
import ClosableDialog from "../components/ClosableDialog";

/**
 * Confirmation before dropping an environment — a delete strips the env AND
 * its scenarios / evals / runs from the store (the reducer's own doing), so a
 * misclick would silently lose the derivation work. Shared by the
 * environments table and the workspace header / Settings danger zone.
 */
export default function DeleteEnvironmentDialog({ env, onCancel, onConfirm }) {
  return (
    <ClosableDialog
      open={!!env}
      onClose={onCancel}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 400 } }}
      fullWidth
    >
      <DialogTitle sx={{ typography: "m2", fontWeight: 600 }}>
        Delete environment?
      </DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ typography: "s2" }}>
          <b>{env?.name}</b>, its scenarios, evaluations and runs will be
          removed. Personas stay. This cannot be undone.
        </DialogContentText>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onCancel} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={() => onConfirm(env)}
          color="error"
        >
          Delete
        </Button>
      </DialogActions>
    </ClosableDialog>
  );
}

DeleteEnvironmentDialog.propTypes = {
  env: PropTypes.object,
  onCancel: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
};
