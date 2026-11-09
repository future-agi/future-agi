import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import { apiErrorMessage } from "../api";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import {
  useCreateMCPCredential,
  useRotateMCPCredential,
} from "../hooks/useMCPCredentials";
import { KINDS, createBody } from "./credentials";

const SECRET_HELP =
  "Write-only: stored encrypted in the vault and never shown again.";

function synced(result, done) {
  if (result?.gateway_synced === false) {
    enqueueSnackbar(
      "Saved in the vault, but the gateway has not confirmed the change. Use Reload config to retry synchronization.",
      { variant: "warning" },
    );
  } else {
    enqueueSnackbar(done, { variant: "success" });
  }
}

function SecretField({ label, value, onChange, disabled = false }) {
  return (
    <TextField
      label={label}
      type="password"
      disabled={disabled}
      autoComplete="new-password"
      size="small"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      helperText={SECRET_HELP}
      fullWidth
    />
  );
}

SecretField.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

const EMPTY = {
  serverId: "",
  kind: "bearer",
  value: "",
  headerName: "",
  label: "",
};

// Store a new shared credential for one of the org's MCP servers. The
// server's config then references it and the gateway sends it upstream.
export function AddCredentialDialog({
  open,
  onClose,
  serverIds,
  taken,
  initialServer,
}) {
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState("");
  const { canEdit, disabledReason } = useGatewayAdmin();
  const create = useCreateMCPCredential();

  useEffect(() => {
    if (open) {
      setForm({ ...EMPTY, serverId: initialServer || "" });
      setError("");
    }
  }, [open, initialServer]);

  const set = (key) => (value) =>
    setForm((prev) => ({ ...prev, [key]: value }));
  const secretLabel = form.kind === "bearer" ? "Token" : "Key";
  const canSave = canEdit && form.serverId && form.value && !create.isPending;

  const save = async () => {
    if (!canSave) return;
    setError("");
    try {
      const result = await create.mutateAsync(createBody(form));
      synced(result, `Credential stored for ${form.serverId}`);
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err, "The credential could not be stored."));
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add credential</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {!canEdit && <Alert severity="info">{disabledReason}</Alert>}
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            select
            label="Server"
            disabled={!canEdit}
            size="small"
            value={form.serverId}
            onChange={(event) => set("serverId")(event.target.value)}
            helperText="One active credential per server: rotate or revoke an existing one instead."
            fullWidth
          >
            {serverIds.map((id) => (
              <MenuItem key={id} value={id} disabled={taken.has(id)}>
                {taken.has(id) ? `${id} (has an active credential)` : id}
              </MenuItem>
            ))}
          </TextField>
          <RadioGroup
            row
            value={form.kind}
            onChange={(event) => set("kind")(event.target.value)}
          >
            {KINDS.map((kind) => (
              <FormControlLabel
                key={kind.value}
                value={kind.value}
                control={<Radio size="small" disabled={!canEdit} />}
                label={kind.label}
              />
            ))}
          </RadioGroup>
          {form.kind === "api_key" && (
            <TextField
              label="Header name"
              disabled={!canEdit}
              size="small"
              value={form.headerName}
              onChange={(event) => set("headerName")(event.target.value)}
              placeholder="X-API-Key"
              helperText="The request header the key is sent in. X-API-Key when left empty."
              fullWidth
            />
          )}
          <SecretField
            label={secretLabel}
            disabled={!canEdit}
            value={form.value}
            onChange={set("value")}
          />
          <TextField
            label="Label"
            disabled={!canEdit}
            size="small"
            value={form.label}
            onChange={(event) => set("label")(event.target.value)}
            placeholder="Production key"
            fullWidth
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={save}
          disabled={!canSave}
        >
          {create.isPending ? "Storing" : "Store credential"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

AddCredentialDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  serverIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  taken: PropTypes.instanceOf(Set).isRequired,
  initialServer: PropTypes.string,
};

// Replace an active credential's secret in place: same id, version + 1.
export function RotateCredentialDialog({ credential, onClose }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const { canEdit, disabledReason } = useGatewayAdmin();
  const rotate = useRotateMCPCredential();

  useEffect(() => {
    setValue("");
    setError("");
  }, [credential]);

  const label =
    credential?.kind === "bearer" ? "New bearer token" : "New API key";

  const save = async () => {
    if (!canEdit || !credential || !value || rotate.isPending) return;
    setError("");
    try {
      const result = await rotate.mutateAsync({
        id: credential.id,
        body: { value },
      });
      synced(result, "Credential rotated");
      onClose();
    } catch (err) {
      setError(apiErrorMessage(err, "The credential could not be rotated."));
    }
  };

  return (
    <Dialog
      open={Boolean(credential)}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>Rotate credential</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {!canEdit && <Alert severity="info">{disabledReason}</Alert>}
          {error && <Alert severity="error">{error}</Alert>}
          <Typography typography="s1" color="text.secondary">
            {credential
              ? `${credential.server_id}: the new value replaces the stored one. The gateway keeps sending the old one until it picks up the change.`
              : ""}
          </Typography>
          <SecretField
            label={label}
            value={value}
            onChange={setValue}
            disabled={!canEdit}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={save}
          disabled={!canEdit || !value || rotate.isPending}
        >
          {rotate.isPending ? "Rotating" : "Rotate"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

RotateCredentialDialog.propTypes = {
  credential: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};
