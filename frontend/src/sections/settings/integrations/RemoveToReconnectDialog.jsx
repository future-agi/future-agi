import React from "react";
import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";
import { connectionName, isDestinationListed } from "./constants";
import { outlinedNeutralButtonSx } from "./styles";
import { storageChoice } from "./utils";
import useOfferedPlatforms from "./useOfferedPlatforms";

// Reconnect, for a connection whose saved credentials can't be read and whose
// credentials Edit can't take again (a message queue, Google Cloud Storage or
// Azure Blob): it says so in plain words and offers to remove the connection.
// Adding it again is offered only where this page offers adding one.
export default function RemoveToReconnectDialog({
  open,
  onClose,
  onRemove,
  connection,
}) {
  const offered = useOfferedPlatforms();
  const choice = storageChoice(connection);
  const kind = connectionName(connection.platform, { sub_platform: choice });
  const addable =
    offered.some((platform) => platform.id === connection.platform) &&
    isDestinationListed(connection.platform, choice);
  const name =
    connection.display_name || connection.external_project_name || kind;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 480 } }}
      fullWidth
    >
      <DialogTitle>Reconnect {name}</DialogTitle>
      <DialogContent>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {addable
            ? "Its credentials can't be entered again in Edit. To reconnect it, remove it, then add it again on this page with its credentials."
            : `Its credentials can't be entered again in Edit, and this page doesn't offer adding ${kind} connections, so it can't be reconnected here. You can remove it.`}
        </Typography>
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
        <Button
          size="small"
          variant="contained"
          color="error"
          onClick={onRemove}
        >
          Remove connection
        </Button>
      </DialogActions>
    </Dialog>
  );
}

RemoveToReconnectDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  connection: PropTypes.object.isRequired,
};
