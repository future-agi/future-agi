/* eslint-disable react/prop-types */
import React, { useState, useEffect } from "react";
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useUpdateMCPServer } from "../hooks/useMCPConfig";
import { hasStoredCredential } from "../utils";

const AUTH_OPTIONS = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer Token" },
  { value: "api_key", label: "API Key" },
];

const SECRET_LABEL = { bearer: "bearer token", api_key: "API key" };

const EMPTY_FORM = {
  server_id: "",
  url: "",
  authType: "none",
  token: "",
  header: "",
  apiKey: "",
};

// The form is filled only from a server's non-secret fields. Credential
// inputs are write-only: they start empty, a stored value is reported only as
// set or not set, and a value is sent only when the user types one.
function formFromServer(editServer) {
  if (!editServer) return EMPTY_FORM;
  const cfg = editServer.config || {};
  return {
    ...EMPTY_FORM,
    server_id: editServer.serverId || editServer.server_id || "",
    url: cfg.url || "",
    authType: cfg.auth?.type || "none",
    header: cfg.auth?.header || "",
  };
}

const AddMCPServerDialog = ({ open, onClose, gatewayId, editServer }) => {
  const isEditing = Boolean(editServer);
  const hasCredential =
    isEditing &&
    (editServer.hasCredential ?? hasStoredCredential(editServer.config));
  const updateMutation = useUpdateMCPServer();

  const [form, setForm] = useState(EMPTY_FORM);

  useEffect(() => {
    setForm(formFromServer(editServer));
  }, [editServer, open]);

  const handleChange = (field) => (e) => {
    setForm((prev) => ({ ...prev, [field]: e.target.value }));
  };

  const secretHelperText = () => {
    if (!isEditing) return "Write-only: it is never shown after you save.";
    if (!hasCredential) return undefined;
    // Saving replaces the server's whole entry, so the stored value is kept
    // only when it is entered again.
    return "A value is stored and is never shown. Enter it again to save this server.";
  };

  const handleSave = () => {
    if (!form.server_id.trim()) {
      enqueueSnackbar("Server ID is required", { variant: "warning" });
      return;
    }
    if (!form.url.trim()) {
      enqueueSnackbar("URL is required", { variant: "warning" });
      return;
    }

    const serverConfig = { transport: "http", url: form.url.trim() };

    if (form.authType !== "none") {
      const secret = (
        form.authType === "bearer" ? form.token : form.apiKey
      ).trim();
      if (!secret) {
        enqueueSnackbar(
          `Enter the ${SECRET_LABEL[form.authType]} to save this server`,
          { variant: "warning" },
        );
        return;
      }
      serverConfig.auth = { type: form.authType };
      if (form.authType === "bearer") {
        serverConfig.auth.token = secret;
      } else {
        serverConfig.auth.header = form.header.trim() || "X-API-Key";
        serverConfig.auth.key = secret;
      }
    }

    updateMutation.mutate(
      {
        gatewayId,
        serverId: form.server_id.trim(),
        config: serverConfig,
      },
      {
        onSuccess: () => {
          enqueueSnackbar(
            isEditing
              ? `Server "${form.server_id}" updated`
              : `Server "${form.server_id}" added`,
            { variant: "success" },
          );
          onClose();
        },
        onError: () => {
          enqueueSnackbar("Failed to save server configuration", {
            variant: "error",
          });
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {isEditing ? "Edit MCP Server" : "Add MCP Server"}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <TextField
            label="Server ID"
            value={form.server_id}
            onChange={handleChange("server_id")}
            disabled={isEditing}
            required
            fullWidth
            size="small"
            helperText="Unique identifier for this server (e.g., github, slack)"
          />

          <TextField
            label="URL"
            value={form.url}
            onChange={handleChange("url")}
            required
            fullWidth
            size="small"
            placeholder="http://mcp-server:8080"
          />

          <TextField
            label="Auth Type"
            value={form.authType}
            onChange={handleChange("authType")}
            select
            fullWidth
            size="small"
          >
            {AUTH_OPTIONS.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>

          {isEditing && (
            <Typography variant="body2" color="text.secondary">
              {hasCredential
                ? "Stored credential: set"
                : "Stored credential: not set"}
            </Typography>
          )}

          {form.authType === "bearer" && (
            <TextField
              label="Bearer Token"
              value={form.token}
              onChange={handleChange("token")}
              fullWidth
              size="small"
              type="password"
              autoComplete="new-password"
              placeholder="Enter token"
              helperText={secretHelperText()}
            />
          )}

          {form.authType === "api_key" && (
            <>
              <TextField
                label="Header Name"
                value={form.header}
                onChange={handleChange("header")}
                fullWidth
                size="small"
                placeholder="X-API-Key"
              />
              <TextField
                label="API Key"
                value={form.apiKey}
                onChange={handleChange("apiKey")}
                fullWidth
                size="small"
                type="password"
                autoComplete="new-password"
                placeholder="Enter key"
                helperText={secretHelperText()}
              />
            </>
          )}

          {updateMutation.isError && (
            <Alert severity="error">
              {updateMutation.error?.message ||
                "Failed to save server configuration"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={updateMutation.isPending}
        >
          {updateMutation.isPending
            ? "Saving..."
            : isEditing
              ? "Update Server"
              : "Add Server"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default AddMCPServerDialog;
