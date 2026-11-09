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
import { reportAlertingSaved } from "./alertingModel";
import { ALERT_CHANNEL_TYPE_OPTIONS } from "../constants/alerting";

const CreateChannelDialog = ({
  open,
  onClose,
  gatewayId,
  existingChannels = [],
}) => {
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
    const channelName = name.trim();
    const channel = { name: channelName, type };
    if (url.trim()) channel.url = url.trim();

    // Send the whole array, for the same reason the rule dialog does: a
    // name-keyed object would replace a channels array saved from
    // Settings → Alerting.
    const channels = [
      ...existingChannels.filter((c) => c?.name !== channelName),
      channel,
    ];

    updateConfig.mutate(
      { gatewayId, config: { alerting: { channels } } },
      {
        onSuccess: (result) => {
          reportAlertingSaved(result, `Channel "${channelName}" created`);
          handleClose();
        },
        onError: () => {
          enqueueSnackbar("The channel could not be created", {
            variant: "error",
          });
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add notification channel</DialogTitle>
      <DialogContent>
        <Stack spacing={2} mt={1}>
          <TextField
            label="Channel name"
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
            {ALERT_CHANNEL_TYPE_OPTIONS.map((t) => (
              <MenuItem key={t.value} value={t.value}>
                {t.label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="URL / Endpoint"
            fullWidth
            required={type !== "log"}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={
              type === "slack" ? "https://hooks.slack.com/..." : "Endpoint URL"
            }
          />
          {updateConfig.isError && (
            <Alert severity="error">
              {updateConfig.error?.message ||
                "The channel could not be created"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={handleCreate}
          disabled={
            !name.trim() ||
            (type !== "log" && !url.trim()) ||
            updateConfig.isPending
          }
        >
          {updateConfig.isPending ? "Creating..." : "Add channel"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateChannelDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
  existingChannels: PropTypes.arrayOf(PropTypes.object),
};

export default CreateChannelDialog;
