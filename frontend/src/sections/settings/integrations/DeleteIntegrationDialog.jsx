import { useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  TextField,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useDeleteConnection } from "src/api/integrations";
import { outlinedNeutralButtonSx } from "./styles";

function DeleteDialogContent({ onClose, onDeleted, connection }) {
  const { mutate: deleteConnection, isPending } = useDeleteConnection();
  const [confirmText, setConfirmText] = useState("");

  const handleDelete = () => {
    deleteConnection(connection.id, {
      onSuccess: () => (onDeleted || onClose)(),
    });
  };

  return (
    <>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          Are you sure you want to delete the connection to{" "}
          <strong>
            {connection?.display_name || connection?.external_project_name}
          </strong>
          ? This will stop syncing and remove the connection configuration.
          Previously synced traces will not be deleted.
        </DialogContentText>
        <TextField
          fullWidth
          size="small"
          placeholder='Type "DELETE" to confirm'
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          autoFocus
        />
      </DialogContent>
      <DialogActions>
        <Button
          size="small"
          variant="outlined"
          onClick={onClose}
          sx={outlinedNeutralButtonSx}
        >
          Cancel
        </Button>
        <LoadingButton
          color="error"
          variant="contained"
          size="small"
          loading={isPending}
          onClick={handleDelete}
          disabled={confirmText !== "DELETE"}
        >
          Delete
        </LoadingButton>
      </DialogActions>
    </>
  );
}

DeleteDialogContent.propTypes = {
  onClose: PropTypes.func.isRequired,
  onDeleted: PropTypes.func,
  connection: PropTypes.object.isRequired,
};

export default function DeleteIntegrationDialog({
  open,
  onClose,
  onDeleted,
  connection,
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 400 } }}
      fullWidth
    >
      <DialogTitle>Delete Integration</DialogTitle>
      {open && (
        <DeleteDialogContent
          onClose={onClose}
          onDeleted={onDeleted}
          connection={connection}
        />
      )}
    </Dialog>
  );
}

DeleteIntegrationDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onDeleted: PropTypes.func,
  connection: PropTypes.object.isRequired,
};
