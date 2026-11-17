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
import {
  TTL_KEYS,
  carriedFields,
  probeAuth,
  probeMessage,
  serverForm,
} from "./catalog";
import {
  SERVER_ID_MAX_LENGTH,
  SERVER_ID_RULE,
  isServerIdAllowed,
  isServerIdRefusal,
} from "./serverIdRule";
import RuntimeNotice from "../runtimeNotice";

const SECRET_LABEL = { bearer: "bearer token", api_key: "API key" };

const NEW_AUTH = [
  { value: "none", label: "None" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key", label: "API key" },
];

const TTL_RULE = "The tools cache TTL is a whole number of seconds.";

// On edit a stored secret is never shown or sent back. Its field says one is
// stored: left blank it is kept, and a typed value replaces it.
const STORED_PLACEHOLDER = "Stored, not shown";
const KEEP_STORED =
  "A credential is stored and never shown. Leave this blank to keep it; typing a new one replaces it.";

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

  // A new server's ID is checked as it is typed, as the save sends it. A saved
  // server's ID is read-only and not checked again: the server accepted it.
  const typedServerId = form.server_id.trim();
  const serverIdBroken =
    !isEditing && typedServerId !== "" && !isServerIdAllowed(typedServerId);

  useEffect(() => {
    setForm(serverForm(editServer, hasCredential));
    setProbeResult(null);
    probe.reset?.();
    // Ignore a request that settles after closing or opening another server.
    return () => {
      probeVersion.current += 1;
    };
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
    if (serverIdBroken) {
      enqueueSnackbar(SERVER_ID_RULE, { variant: "warning" });
      return;
    }
    if (!form.url.trim()) {
      enqueueSnackbar("URL is required", { variant: "warning" });
      return;
    }

    // An edit that leaves the TTL as it opened sends the stored value back,
    // under the name it was stored with (carriedFields).
    const ttl = form.toolsCacheTtl.trim();
    const ttlChanged =
      !isEditing || ttl !== serverForm(editServer, hasCredential).toolsCacheTtl;
    if (ttlChanged && ttl && !/^\d+$/.test(ttl)) {
      enqueueSnackbar(TTL_RULE, { variant: "warning" });
      return;
    }

    const serverConfig = {
      ...(isEditing ? carriedFields(editServer.config) : {}),
      ...(isEditing && form.enabled !== undefined
        ? { enabled: form.enabled }
        : {}),
      transport,
      url: form.url.trim(),
    };
    if (ttlChanged) {
      // A changed TTL is stored in seconds alone. Empty sends no value: the
      // gateway's default, 300 seconds (as 0 is).
      TTL_KEYS.forEach((key) => delete serverConfig[key]);
      if (ttl) serverConfig.tools_cache_ttl_seconds = Number(ttl);
    }

    if (form.authType === "bearer" || form.authType === "api_key") {
      const secret = (
        form.authType === "bearer" ? form.token : form.apiKey
      ).trim();
      if (secret) {
        serverConfig.auth = { type: form.authType };
        if (form.authType === "bearer") {
          serverConfig.auth.token = secret;
        } else {
          serverConfig.auth.header = form.header.trim() || "X-API-Key";
          serverConfig.auth.key = secret;
        }
      } else if (!hasCredential) {
        enqueueSnackbar(
          `Enter the ${SECRET_LABEL[form.authType]} to save this server`,
          { variant: "warning" },
        );
        return;
      } else if (form.authType === "api_key") {
        // A blank key keeps the stored one. A new header name is sent alone:
        // the stored key goes in that header from now on. Otherwise no auth
        // is sent, and the backend keeps the stored credential.
        const header = form.header.trim();
        if (header && header !== serverForm(editServer, hasCredential).header) {
          serverConfig.auth = { type: "api_key", header };
        }
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
        onError: (error) => {
          const message = isServerIdRefusal(error)
            ? SERVER_ID_RULE
            : "Failed to save server configuration";
          enqueueSnackbar(message, { variant: "error" });
        },
      },
    );
  };

  const authOptions = hasCredential ? STORED_AUTH : NEW_AUTH;
  const saveError = isServerIdRefusal(updateMutation.error)
    ? SERVER_ID_RULE
    : updateMutation.error?.message || "Failed to save server configuration";

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
            error={serverIdBroken}
            inputProps={{ maxLength: SERVER_ID_MAX_LENGTH }}
            helperText={
              isEditing
                ? "A server's ID does not change; its tools are named <id>__<tool>."
                : `A short name, for example github or crm. ${SERVER_ID_RULE} Its tools are named <id>__<tool>.`
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
              placeholder={hasCredential ? STORED_PLACEHOLDER : undefined}
              helperText={
                hasCredential
                  ? `${KEEP_STORED} Format: Authorization: Bearer <token>.`
                  : "Format: Authorization: Bearer <token>. Write-only: stored in the vault, never shown again."
              }
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
                helperText={
                  hasCredential
                    ? "Left blank, the stored header is kept. A new name keeps the stored key, sent in that header."
                    : undefined
                }
              />
              <TextField
                label="Key"
                value={form.apiKey}
                onChange={handleChange("apiKey")}
                fullWidth
                size="small"
                type="password"
                autoComplete="new-password"
                placeholder={hasCredential ? STORED_PLACEHOLDER : undefined}
                helperText={
                  hasCredential
                    ? KEEP_STORED
                    : "Write-only: stored in the vault, never shown again."
                }
              />
            </>
          )}

          <TextField
            label="Tools cache TTL (seconds)"
            value={form.toolsCacheTtl}
            onChange={handleChange("toolsCacheTtl")}
            fullWidth
            size="small"
            inputProps={{ inputMode: "numeric" }}
            helperText="How often the gateway re-reads this server's tools, resources and prompts, besides when the server reports a change. Empty or 0 is 300 seconds."
          />

          {isEditing && (
            <Box>
              <FormControlLabel
                label="Enabled"
                control={
                  <Switch
                    checked={form.enabled !== false}
                    disabled={!canEdit || updateMutation.isPending}
                    onChange={(_, enabled) =>
                      setForm((prev) => ({ ...prev, enabled }))
                    }
                  />
                }
              />
              <Typography typography="s2" color="text.secondary">
                Allow this server to connect when MCP is on. Saving does not
                turn MCP on.
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
              {saveError}
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
              : probeResult?.ok && !runtimeOff
                ? "Save server"
                : "Add server"}
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
