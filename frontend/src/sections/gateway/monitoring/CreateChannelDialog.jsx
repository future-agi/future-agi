import React, { useEffect, useState } from "react";
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

function channelName(channel, index) {
  return channel?.name || `Channel ${index + 1}`;
}

function channelsToMap(channels) {
  return Object.fromEntries(
    channels.map((channel, index) => {
      const name = channelName(channel, index);
      return [name, { ...channel, name }];
    }),
  );
}

const CreateChannelDialog = ({
  open,
  onClose,
  gatewayId,
  initialChannel = null,
  existingChannels = [],
  replaceChannels = false,
}) => {
  const isEdit = Boolean(initialChannel);
  const [name, setName] = useState("");
  const [type, setType] = useState("webhook");
  const [url, setUrl] = useState("");

  const updateConfig = useUpdateConfig();

  useEffect(() => {
    if (!open) return;
    setName(initialChannel?.name || "");
    setType(initialChannel?.type || "webhook");
    setUrl(
      initialChannel?.url ||
        initialChannel?.endpoint ||
        initialChannel?.webhook_url ||
        "",
    );
  }, [initialChannel, open]);

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
    const originalName = initialChannel?.name;
    const channelsPatch = replaceChannels
      ? channelsToMap(existingChannels)
      : {};
    if (originalName && originalName !== name) {
      if (replaceChannels) {
        delete channelsPatch[originalName];
      } else {
        channelsPatch[originalName] = null;
      }
    }
    channelsPatch[name] = channel;

    updateConfig.mutate(
      {
        gatewayId,
        config: {
          alerting: {
            channels: channelsPatch,
          },
        },
      },
      {
        onSuccess: () => {
          enqueueSnackbar(
            `Channel "${name}" ${isEdit ? "updated" : "created"}`,
            {
              variant: "success",
            },
          );
          handleClose();
        },
        onError: () => {
          enqueueSnackbar(`Failed to ${isEdit ? "update" : "create"} channel`, {
            variant: "error",
          });
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {isEdit ? "Edit Notification Channel" : "Add Notification Channel"}
      </DialogTitle>
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
          {updateConfig.isPending
            ? isEdit
              ? "Saving..."
              : "Creating..."
            : isEdit
              ? "Save Channel"
              : "Add Channel"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateChannelDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
  initialChannel: PropTypes.object,
  existingChannels: PropTypes.arrayOf(PropTypes.object),
  replaceChannels: PropTypes.bool,
};

export default CreateChannelDialog;
