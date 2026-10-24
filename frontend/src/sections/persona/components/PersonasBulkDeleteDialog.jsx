import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
} from "@mui/material";
import PropTypes from "prop-types";

const PersonasBulkDeleteDialog = ({
  open,
  count,
  skippedCount,
  usageNote,
  onConfirm,
  onCancel,
  isLoading,
}) => (
  <Dialog
    open={open}
    onClose={onCancel}
    maxWidth={false}
    PaperProps={{ sx: { maxWidth: 400 } }}
    fullWidth
  >
    <DialogTitle>
      Delete {count} persona{count !== 1 ? "s" : ""}?
    </DialogTitle>
    <DialogContent>
      <DialogContentText>
        This action cannot be undone.
        {skippedCount > 0 && (
          <>
            {" "}
            {skippedCount} Future AGI built-in persona
            {skippedCount !== 1 ? "s" : ""} will be skipped.
          </>
        )}
        {usageNote && (
          <>
            <br />
            <br />
            {usageNote}
          </>
        )}
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
        disabled={isLoading || count === 0}
      >
        {isLoading ? "Deleting..." : "Delete"}
      </Button>
    </DialogActions>
  </Dialog>
);

PersonasBulkDeleteDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  count: PropTypes.number.isRequired,
  skippedCount: PropTypes.number,
  usageNote: PropTypes.string,
  onConfirm: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  isLoading: PropTypes.bool,
};

export default PersonasBulkDeleteDialog;
