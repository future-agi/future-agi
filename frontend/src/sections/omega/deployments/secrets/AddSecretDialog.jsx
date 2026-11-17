import React, { useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import {
  SECRET_KINDS,
  addSecretProblems,
  afterFailure,
} from "../lib/secretForm";
import SecretValueField from "./SecretValueField";
import { useAddSecret } from "./useSecrets";

const EMPTY = { name: "", kind: "api_key", value: "" };

/**
 * Add one secret to the workspace vault. Problems show after the first
 * attempt to save (or as soon as a name is typed), so an empty form is calm.
 * On success the value is cleared from state and `onAdded(record)` gets the
 * value-free record (the wizard ticks it).
 */
export default function AddSecretDialog({
  open,
  takenNames,
  onClose,
  onAdded,
}) {
  const [form, setForm] = useState(EMPTY);
  const [tried, setTried] = useState(false);
  const add = useAddSecret();
  const problems = addSecretProblems(form, takenNames);
  const show = (field) =>
    tried || form[field] ? problems[`${field}Error`] : null;

  const close = () => {
    if (add.isPending) return;
    setForm(EMPTY);
    setTried(false);
    add.reset();
    onClose();
  };
  const save = (event) => {
    event.preventDefault();
    setTried(true);
    if (!problems.ok || add.isPending) return;
    add.mutate(form, {
      onSuccess: (record) => {
        setForm(EMPTY);
        setTried(false);
        add.reset();
        onAdded?.(record);
        onClose();
      },
    });
  };

  return (
    <Dialog open={open} onClose={close} maxWidth="xs" fullWidth>
      <form onSubmit={save} noValidate data-testid="add-secret-form">
        <DialogTitle sx={{ typography: "m3" }}>Add a secret</DialogTitle>
        <DialogContent
          sx={{ display: "flex", flexDirection: "column", gap: 2 }}
        >
          <Typography typography="s2" color="text.secondary">
            Saved to this workspace&apos;s vault. A deployment that picks it
            reads the value as an environment variable with this name.
          </Typography>
          <TextField
            size="small"
            label="Name"
            autoFocus
            value={form.name}
            disabled={add.isPending}
            onChange={(e) =>
              setForm({ ...form, name: e.target.value.trim().toUpperCase() })
            }
            error={Boolean(show("name"))}
            helperText={show("name") || "For example OPENAI_API_KEY."}
            inputProps={{
              "aria-label": "Secret name",
              "data-testid": "secret-name",
              spellCheck: false,
              autoComplete: "off",
            }}
            InputProps={{ sx: { fontFamily: "monospace" } }}
          />
          <TextField
            select
            size="small"
            label="Kind"
            value={form.kind}
            disabled={add.isPending}
            onChange={(e) => setForm({ ...form, kind: e.target.value })}
            SelectProps={{ inputProps: { "aria-label": "Secret kind" } }}
          >
            {SECRET_KINDS.map((kind) => (
              <MenuItem key={kind.value} value={kind.value}>
                {kind.label}
              </MenuItem>
            ))}
          </TextField>
          <SecretValueField
            value={form.value}
            disabled={add.isPending}
            onChange={(value) => setForm({ ...form, value })}
            error={tried ? problems.valueError : null}
          />
          {add.error && (
            <Alert
              severity="error"
              variant="outlined"
              data-testid="add-secret-error"
            >
              {add.error.message}{" "}
              {afterFailure(add.error, {
                refused: "Nothing was saved.",
                unknown: "Save again: the vault adds the same entry only once.",
              })}
            </Alert>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button color="inherit" onClick={close} disabled={add.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="contained"
            disabled={add.isPending}
            data-testid="save-secret"
          >
            {add.isPending ? "Saving…" : "Save secret"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

AddSecretDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  takenNames: PropTypes.instanceOf(Set).isRequired,
  onClose: PropTypes.func.isRequired,
  onAdded: PropTypes.func,
};
