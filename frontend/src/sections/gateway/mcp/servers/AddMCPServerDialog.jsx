import React, { useEffect, useRef, useState } from "react";
import FormControlLabel from "@mui/material/FormControlLabel";
import Switch from "@mui/material/Switch";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { isSavedButNotApplied } from "../../providers/hooks/useGatewayConfig";

import { GatewayStatusChip } from "../../components/frame";
import { apiErrorMessage } from "../api";
import { useUpdateMCPServer } from "../hooks/useMCPConfig";
import { useMCPProbe } from "../hooks/useMCPProbe";
import { hasStoredCredential } from "../utils";
import { carriedFields, probeAuth, probeMessage, serverForm } from "./catalog";
import RuntimeNotice from "../runtimeNotice";

const SECRET_LABEL = { bearer: "bearer token", api_key: "API key" };

const NEW_AUTH = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key", label: "API key" },
];

const STORED_AUTH = [
  { value: "keep", label: "Keep the stored credential" },
  { value: "bearer", label: "Bearer token (replace the stored one)" },
  { value: "api_key", label: "API key (replace the stored one)" },
  { value: "none", label: "None (Remove the stored credential)" },
];

// Save server settings independently of the live connection probe.
// Typed credentials remain write-only; saving does not activate MCP.
const AddMCPServerDialog = ({
  open,
  onClose,
  gatewayId,
  editServer,
  runtimeOff = false,
  canEdit = true,
}) => {
  const isEditing = Boolean(editServer);
  // Transport is not editable here; existing SSE servers must stay SSE.
  const transport = editServer?.config?.transport || "http";
  const hasCredential =
    isEditing &&
    (editServer.hasCredential ?? hasStoredCredential(editServer.config));
  const updateMutation = useUpdateMCPServer();
  const probe = useMCPProbe();

  const [form, setForm] = useState(() => serverForm());
  const [probeResult, setProbeResult] = useState(null);
  const probeVersion = useRef(0);
  const probeAllowed = useRef(false);
  probeAllowed.current = open && !runtimeOff && canEdit;

  useEffect(() => {
    setForm(serverForm(editServer, hasCredential));
    setProbeResult(null);
    probe.reset?.();
    // Ignore a request that settles after closing or opening another server.
    return () => { probeVersion.current += 1; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editServer, open, hasCredential]);

  const handleChange = (field) => (e) => {
    setForm((prev) => ({ ...prev, [field]: e.target.value }));
    if (["url", "authType", "token", "apiKey", "header"].includes(field)) {
      probeVersion.current += 1;
      setProbeResult(null);
    }
  };

  const testConnection = async () => {
    if (!probeAllowed.current || probe.isPending || !form.url.trim()) return;
    const version = ++probeVersion.current;
    setProbeResult(null);
    try {
      const result = await probe.mutateAsync({
        url: form.url.trim(),
        transport,
        ...probeAuth(form, editServer),
      });
      if (version === probeVersion.current && probeAllowed.current) {
        setProbeResult(probeMessage(result));
      }
    } catch (err) {
      if (version !== probeVersion.current || !probeAllowed.current) return;
      setProbeResult({
        ok: false,
        text: apiErrorMessage(err, "The connection test could not run."),
      });
    }
  };

  const handleSave = () => {
    if (!canEdit || updateMutation.isPending) return;
    if (!form.server_id.trim()) {
      enqueueSnackbar("Server ID is required", { variant: "warning" });
      return;
    }
    if (!form.url.trim()) {
      enqueueSnackbar("URL is required", { variant: "warning" });
      return;
    }

    const serverConfig = {
      ...(isEditing ? carriedFields(editServer.config) : {}),
      ...(isEditing && form.enabled !== undefined ? { enabled: form.enabled } : {}),
      transport,
      url: form.url.trim(),
    };

    if (form.authType === "bearer" || form.authType === "api_key") {
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
    } else if (form.authType === "none" && hasCredential) {
      // The explicit way to drop a stored credential: the vault revokes it.
      serverConfig.auth = { type: "none" };
    }

    updateMutation.mutate(
      {
        gatewayId,
        serverId: form.server_id.trim(),
        config: serverConfig,
      },
      {
        onSuccess: (result) => {
          if (result?.gateway_synced === false) {
            enqueueSnackbar(
              result.gateway_warning ||
                "Saved. The gateway has not picked it up yet.",
              { variant: "warning" },
            );
          } else {
            enqueueSnackbar(
              isEditing
                ? `Server "${form.server_id}" saved`
                : `Server "${form.server_id}" added`,
              { variant: "success" },
            );
          }
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

  const authOptions = hasCredential ? STORED_AUTH : NEW_AUTH;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle sx={{ typography: "m3", fontWeight: 500 }}>
        {isEditing
          ? `Edit ${form.server_id || "MCP server"}`
          : "Add MCP server"}
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
            helperText={
              isEditing
                ? "A server's ID does not change; its tools are named <id>__<tool>."
                : "A short name, for example github or crm. Its tools are named <id>__<tool>."
            }
          />

          <TextField
            label="URL"
            value={form.url}
            onChange={handleChange("url")}
            required
            fullWidth
            size="small"
            placeholder="https://mcp.example.com/mcp"
            helperText={`The server's MCP endpoint (${transport === "sse" ? "HTTP+SSE" : "Streamable HTTP"}). Include its path. Saving this URL does not connect the server while MCP is off.`}
          />

          <TextField
            label="Authentication"
            value={form.authType}
            onChange={handleChange("authType")}
            select
            fullWidth
            size="small"
            SelectProps={{ MenuProps: { disableScrollLock: true } }}
          >
            {authOptions.map((opt) => (
              <MenuItem key={opt.value} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>

          {isEditing && (
            <Typography typography="s2" color="text.secondary">
              {hasCredential
                ? "Stored credential: set. It is never shown; keep it, replace it or remove it."
                : "Stored credential: not set"}
            </Typography>
          )}

          {form.authType === "bearer" && (
            <TextField
              label="Token"
              value={form.token}
              onChange={handleChange("token")}
              fullWidth
              size="small"
              type="password"
              autoComplete="new-password"
              helperText="Format: Authorization: Bearer <token>. Write-only: stored in the vault, never shown again."
            />
          )}

          {form.authType === "api_key" && (
            <>
              <TextField
                label="Header name"
                value={form.header}
                onChange={handleChange("header")}
                fullWidth
                size="small"
                placeholder="X-API-Key"
              />
              <TextField
                label="Key"
                value={form.apiKey}
                onChange={handleChange("apiKey")}
                fullWidth
                size="small"
                type="password"
                autoComplete="new-password"
                helperText="Write-only: stored in the vault, never shown again."
              />
            </>
          )}

          {isEditing && (
            <Box>
              <FormControlLabel label="Enabled" control={
                <Switch checked={form.enabled !== false}
                  disabled={!canEdit || updateMutation.isPending}
                  onChange={(_, enabled) => setForm((prev) => ({ ...prev, enabled }))} />
              } />
              <Typography typography="s2" color="text.secondary">
                Allow this server to connect when MCP is on. Saving does not turn MCP on.
              </Typography>
            </Box>
          )}
          {runtimeOff && <RuntimeNotice />}
          {!runtimeOff && probeResult && (
            <Box
              role="status"
              sx={{
                display: "flex",
                gap: 1,
                alignItems: "flex-start",
                p: 1.25,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                bgcolor: "background.neutral",
              }}
            >
              <GatewayStatusChip
                tone={probeResult.ok ? "healthy" : "error"}
                label={probeResult.ok ? "Test passed" : "Test failed"}
              />
              <Typography
                typography="s2"
                sx={{ pt: "2px", overflowWrap: "anywhere" }}
              >
                {probeResult.text}
              </Typography>
            </Box>
          )}

          {updateMutation.isError && (
            <Alert
              severity={
                isSavedButNotApplied(updateMutation.error) ? "warning" : "error"
              }
            >
              {updateMutation.error?.message ||
                "Failed to save server configuration"}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ flexWrap: "wrap", gap: 1 }}>
        <Button
          variant="outlined"
          onClick={testConnection}
          disabled={
            runtimeOff || !canEdit || !form.url.trim() || probe.isPending
          }
          startIcon={
            probe.isPending ? (
              <CircularProgress size={14} color="inherit" />
            ) : null
          }
          sx={{ mr: "auto" }}
        >
          Test connection
        </Button>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={handleSave}
          disabled={!canEdit || updateMutation.isPending}
        >
          {updateMutation.isPending
            ? "Saving..."
            : isEditing
              ? "Save"
              : probeResult?.ok && !runtimeOff ? "Save server" : "Add server"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

AddMCPServerDialog.propTypes = {
  runtimeOff: PropTypes.bool,
  canEdit: PropTypes.bool,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  gatewayId: PropTypes.string,
  editServer: PropTypes.shape({
    serverId: PropTypes.string,
    server_id: PropTypes.string,
    config: PropTypes.object,
    hasCredential: PropTypes.bool,
    storedHeader: PropTypes.string,
  }),
};

export default AddMCPServerDialog;
