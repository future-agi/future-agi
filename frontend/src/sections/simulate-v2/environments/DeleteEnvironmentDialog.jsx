import PropTypes from "prop-types";
import {
  Button, Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
} from "@mui/material";

/**
 * Confirmation before dropping an environment — a delete strips the env AND
 * its scenarios / evals / runs from the store (the reducer's own doing), so a
 * misclick would silently lose the derivation work. Shared by the
 * environments table and the workspace header / Settings danger zone.
 */
export default function DeleteEnvironmentDialog({ env, onCancel, onConfirm }) {
  return (
    <Dialog open={!!env} onClose={onCancel} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ typography: "m2", fontWeight: 700 }}>
        Delete environment?
      </DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ typography: "s2" }}>
          <b>{env?.name}</b> and everything derived from it —
          scenarios, personas, evals and run history — will be removed
          from this workspace. This cannot be undone.
        </DialogContentText>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button
          onClick={onCancel}
          sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={() => onConfirm(env)}
          sx={{
            typography: "s2", fontWeight: 700,
            bgcolor: "#DC2626", color: "#fff",
            "&:hover": { bgcolor: "#B91C1C", color: "#fff" },
          }}
        >
          Delete
        </Button>
      </DialogActions>
    </Dialog>
  );
}

DeleteEnvironmentDialog.propTypes = {
  env: PropTypes.object,
  onCancel: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
};
