import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  Alert,
  MenuItem,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useUpdateConfig } from "../providers/hooks/useGatewayConfig";

const CHANNEL_TYPES = ["webhook", "email", "slack", "pagerduty"];

const CreateChannelDialog = ({ open, onClose, gatewayId }) => {
  const [name, setName] = useState("");
  const [type, setType] = useState("webhook");
  const [url, setUrl] = useState("");

  const updateConfig = useUpdateConfig();

  const resetForm = () => {
    setName("");
    setType("webhook");
    setUrl("");
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const handleCreate = () => {
    const channel = { name, type, url };

    updateConfig.mutate(
      {
        gatewayId,
        config: {
          alerting: {
            channels: { [name]: channel },
          },
        },
      },
      {
        onSuccess: () => {
          enqueueSnackbar(`Channel "${name}" created`, { variant: "success" });
          handleClose();
        },
        onError: () => {
          enqueueSnackbar("Failed to create channel", { variant: "error" });
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>Add Notification Channel</DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          <TextField
            label="Channel Name"
            fullWidth
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g., slack-alerts"
          />
          <TextField
            label="Type"
            select
            fullWidth
            value={type}
            onChange={(e) => setType(e.target.value)}
          >
            {CHANNEL_TYPES.map((t) => (
              <MenuItem key={t} value={t}>
                {t}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="URL / Endpoint"
            fullWidth
            required
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={
              type === "webhook"
                ? "https://hooks.slack.com/..."
                : type === "email"
                  ? "alerts@company.com"
                  : "Endpoint URL"
            }
          />
          {updateConfig.isError && (
            <Alert severity="error">
              {updateConfig.error?.message || "Failed to create channel"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleCreate}
          disabled={!name.trim() || !url.trim() || updateConfig.isPending}
        >
          {updateConfig.isPending ? "Creating..." : "Add Channel"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateChannelDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
};

export default CreateChannelDialog;
