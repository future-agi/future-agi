import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  FormControl,
  FormControlLabel,
  FormLabel,
  Radio,
  RadioGroup,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { useSafeAuthContext } from "src/auth/hooks/use-auth-context";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { copyText } from "src/sections/omega/code/clipboard";
import { NO_RECORDING_CLASS } from "src/sections/omega/code/noRecording";
import {
  DEFAULT_KEY_ACCESS,
  KEY_ACCESS,
  createKeyBody,
  keyAccessLabel,
  keyScopesShown,
} from "./keyScopes";

// A name a person would write, in the viewer's locale ("Personal key, Nov 10,
// 2:15:30 PM"). The API refuses a name you already use, so the time reads to
// the second, and a name already in the list the page has loaded gets a
// number ("... (2)", "... (3)") so keys made back to back still get a free one.
function defaultKeyName(date = new Date(), takenNames = []) {
  const when = new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  })
    .format(date)
    .replace(/\s+/g, " ");
  const base = `Personal key, ${when}`;
  const taken = new Set(Array.isArray(takenNames) ? takenNames : []);
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base} (${n})`)) n += 1;
  return `${base} (${n})`;
}

// A dialog's reply lives only in this mounted instance, never a query/mutation cache.
function KeyCreation({
  onClose,
  onCreated,
  refreshGrid,
  organizationId,
  workspaceId,
  workspaceName,
  existingKeyNames,
  defaultWorkspaceId,
}) {
  const [name, setName] = useState(() =>
    defaultKeyName(new Date(), existingKeyNames),
  );
  const [workspace, setWorkspace] = useState(defaultWorkspaceId || "");
  const scopesShown = keyScopesShown();
  const [access, setAccess] = useState(DEFAULT_KEY_ACCESS);
  const [pair, setPair] = useState(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const busy = useRef(false);
  const dialog = useRef(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const create = async () => {
    if (busy.current || pair || !name.trim()) return;
    busy.current = true;
    setPending(true);
    setError("");
    try {
      const response = await axios.post(
        endpoints.keys.generateSecretKey,
        {
          ...(scopesShown
            ? createKeyBody(name.trim(), access)
            : { key_name: name.trim() }),
          // The issuer binds from this field, never from the request's workspace
          // header. Explicit null also prevents an ambient workspace default.
          workspace_id: workspace || null,
        },
        {
          headers: {
            "X-Organization-Id": organizationId,
            "X-Workspace-Id": workspace || null,
          },
        },
      );
      if (!mounted.current) return;
      const result = response?.data?.result;
      const apiKey = result?.api_key ?? result?.apiKey;
      const secretKey = result?.secret_key ?? result?.secretKey;
      if (
        typeof apiKey !== "string" ||
        typeof secretKey !== "string" ||
        !/^[a-zA-Z0-9_-]+$/.test(apiKey) ||
        !/^[a-zA-Z0-9_-]+$/.test(secretKey) ||
        response?.data?.status === false
      ) {
        // Do not offer a blind retry of a successful mint with an unreadable reply.
        setError(
          "The key response could not be read. Check your API keys before creating another key.",
        );
        return;
      }
      setPair({
        apiKey,
        secretKey,
        ...(scopesShown ? { scopes: result.scopes } : {}),
      });
      refreshGrid?.();
    } catch (failure) {
      if (mounted.current) {
        const data = failure?.response?.data || failure;
        const membershipRefused =
          (failure?.statusCode ?? failure?.response?.status) === 403 &&
          data?.detail === "Active workspace membership is required.";
        setError(
          membershipRefused
            ? 'Active workspace membership is required. Choose "No workspace restriction" or ask a workspace admin to add you, then try again.'
            : "The key could not be confirmed. Check your API keys before trying again.",
        );
        busy.current = false;
      }
    } finally {
      if (mounted.current) setPending(false);
    }
  };
  const copy = async (text) => {
    const copied = await copyText(text, dialog.current);
    if (mounted.current)
      setCopyStatus(
        copied ? "Copied" : "Copy failed. Select and copy the value.",
      );
  };
  const close = () => {
    if (!busy.current || pair || !pending) onClose();
  };
  const saved = () => {
    onCreated?.(pair);
    onClose();
  };
  const env = pair
    ? `export FI_API_KEY='${pair.apiKey}' FI_SECRET_KEY='${pair.secretKey}'`
    : "";

  return (
    <Dialog
      open
      onClose={pending ? undefined : close}
      fullWidth
      maxWidth={false}
      aria-labelledby="api-key-dialog"
      PaperProps={{
        className: NO_RECORDING_CLASS,
        ref: dialog,
        sx: { maxWidth: 720 },
      }}
    >
      <DialogTitle
        id="api-key-dialog"
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 1,
        }}
      >
        <Typography
          component="span"
          color="text.primary"
          fontWeight={600}
          fontSize="16px"
        >
          {pair ? "Save your personal key" : "Create a personal key"}
        </Typography>
        <IconButton
          aria-label="Close key dialog"
          disabled={pending}
          onClick={close}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          {pair ? (
            <>
              <Typography typography="s1" color="text.secondary">
                These values are shown once. Save both before closing.
              </Typography>
              {[
                ["API key", pair.apiKey],
                ["Secret key", pair.secretKey],
              ].map(([label, value]) => (
                <Box
                  key={label}
                  sx={{ display: "flex", gap: 1, alignItems: "center" }}
                >
                  <TextField
                    label={label}
                    value={value}
                    fullWidth
                    size="small"
                    InputProps={{ readOnly: true }}
                  />
                  <Button
                    onClick={() => copy(value)}
                    aria-label={`Copy ${label}`}
                  >
                    Copy
                  </Button>
                </Box>
              ))}
              <TextField
                label="Shell environment"
                value={env}
                multiline
                fullWidth
                InputProps={{ readOnly: true }}
              />
              <Button onClick={() => copy(env)}>Copy export snippet</Button>
              {scopesShown && (
                <Typography
                  typography="s2"
                  color="text.secondary"
                  data-testid="created-key-access"
                >
                  Access: {keyAccessLabel(pair.scopes)}
                </Typography>
              )}
              <Typography role="status" typography="s2">
                {copyStatus}
              </Typography>
            </>
          ) : (
            <>
              <TextField
                autoFocus
                label="Key name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    create();
                  }
                }}
                fullWidth
                required
                size="small"
                disabled={pending}
              />
              <TextField
                select
                label="Workspace (optional)"
                value={workspace}
                onChange={(e) => setWorkspace(e.target.value)}
                size="small"
                disabled={pending}
                // The empty choice reads "No workspace restriction" in the box,
                // with the label held above it.
                SelectProps={{ displayEmpty: true }}
                InputLabelProps={{ shrink: true }}
              >
                <MenuItem value="">No workspace restriction</MenuItem>
                {workspaceId && (
                  <MenuItem value={workspaceId}>
                    {workspaceName || "Current workspace"}
                  </MenuItem>
                )}
              </TextField>
              {scopesShown && (
                <FormControl disabled={pending} data-testid="key-access">
                  <FormLabel id="key-access-label">Access</FormLabel>
                  <RadioGroup
                    aria-labelledby="key-access-label"
                    value={access}
                    onChange={(e) => setAccess(e.target.value)}
                  >
                    {KEY_ACCESS.map((choice) => (
                      <FormControlLabel
                        key={choice.value}
                        value={choice.value}
                        control={<Radio size="small" />}
                        label={
                          <Box>
                            <Typography typography="s1">
                              {choice.label}
                            </Typography>
                            {choice.help && (
                              <Typography
                                typography="s2"
                                color="text.secondary"
                              >
                                {choice.help}
                              </Typography>
                            )}
                          </Box>
                        }
                      />
                    ))}
                  </RadioGroup>
                </FormControl>
              )}
              <Typography typography="s2" color="text.secondary">
                API keys are personal. Access follows your membership and role.
              </Typography>
            </>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        {pair ? (
          <Button variant="contained" onClick={saved}>
            I saved it
          </Button>
        ) : (
          <>
            <Button disabled={pending} onClick={close}>
              Cancel
            </Button>
            <LoadingButton
              loading={pending}
              disabled={!name.trim() || busy.current}
              variant="contained"
              onClick={create}
            >
              Create a key
            </LoadingButton>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
}

export default function CreateApiKey({
  open,
  onClose,
  refreshGrid,
  onCreated,
  existingKeyNames,
}) {
  const { user } = useSafeAuthContext();
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId, currentWorkspaceName } = useWorkspace();
  if (!open || !user?.id || !currentOrganizationId) return null;
  // For non-global Viewer/Member levels, user-info's ws_level requires an
  // active workspace membership. Owner/Admin effective levels do not prove
  // membership, so keep them (and unknown/stale scopes) unrestricted by default.
  const defaultWorkspaceId =
    user.organization?.id === currentOrganizationId &&
    user.default_workspace_id === currentWorkspaceId &&
    [1, 3].includes(user.org_level) &&
    user.ws_level != null
      ? currentWorkspaceId
      : null;
  return (
    <KeyCreation
      key={`${user?.id}:${currentOrganizationId}:${currentWorkspaceId}`}
      onClose={onClose}
      refreshGrid={refreshGrid}
      onCreated={onCreated}
      organizationId={currentOrganizationId}
      workspaceId={currentWorkspaceId}
      workspaceName={currentWorkspaceName}
      existingKeyNames={existingKeyNames}
      defaultWorkspaceId={defaultWorkspaceId}
    />
  );
}
CreateApiKey.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  refreshGrid: PropTypes.func,
  onCreated: PropTypes.func,
  // Names of the keys the page has loaded; the default name avoids them.
  existingKeyNames: PropTypes.arrayOf(PropTypes.string),
};
KeyCreation.propTypes = {
  onClose: PropTypes.func.isRequired,
  onCreated: PropTypes.func,
  refreshGrid: PropTypes.func,
  organizationId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  workspaceId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  workspaceName: PropTypes.string,
  existingKeyNames: PropTypes.arrayOf(PropTypes.string),
  defaultWorkspaceId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};
