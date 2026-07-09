import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
} from "@mui/material";
import PropTypes from "prop-types";

const BulkDeleteDialog = ({ open, count, onConfirm, onCancel, isLoading }) => (
  <Dialog open={open} onClose={onCancel} maxWidth="xs" fullWidth>
    <DialogTitle>
      Delete {count} evaluation{count !== 1 ? "s" : ""}?
    </DialogTitle>
    <DialogContent>
      <DialogContentText>
        This action cannot be undone. System evaluations will not be deleted.
      </DialogContentText>
      <DialogContentText sx={{ mt: 1, fontSize: "0.8rem", color: "text.secondary" }}>
        This will also remove evaluation results from linked datasets.
      </DialogContentText>
    </DialogContent>
    <DialogActions>
      <Button onClick={onCancel} disabled={isLoading}>
        Cancel
      </Button>
      <Button
        onClick={onConfirm}
        color="error"
        variant="contained"
        disabled={isLoading}
      >
        {isLoading ? "Deleting..." : "Delete"}
      </Button>
    </DialogActions>
  </Dialog>
);

BulkDeleteDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  count: PropTypes.number.isRequired,
  onConfirm: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  isLoading: PropTypes.bool,
};

export default BulkDeleteDialog;
