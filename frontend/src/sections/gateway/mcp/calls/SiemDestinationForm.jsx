import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import ConfirmDialog from "../../components/ConfirmDialog";
import { GATEWAY_MONO } from "../../utils/fonts";
import { apiErrorMessage } from "../api";
import { siemWrites, useSiemWrite } from "../hooks/useSiemExport";
import { SIEM_KINDS, SIEM_STREAMS } from "./siemOptions";

const EMPTY = {
  name: "",
  kind: "webhook",
  url: "",
  streams: ["audit_events"],
  authHeader: "",
  authValue: "",
  token: "",
};
const FIELD_SX = { "& .MuiOutlinedInput-root": { typography: "s2" } };

// Credentials live only in this mounted form; no browser storage. An edit
// starts with a blank password field: blank means keep the existing secret.
export default function SiemDestinationForm({
  disabled,
  destination,
  onSaved,
}) {
  const editing = Boolean(destination);
  const [form, setForm] = useState(() =>
    destination
      ? { ...EMPTY, ...destination, authHeader: destination.auth_header || "" }
      : EMPTY,
  );
  const [created, setCreated] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [connection, setConnection] = useState(null);
  const write = useSiemWrite(editing ? siemWrites.update : siemWrites.create);
  const probe = useSiemWrite(siemWrites.testConnection);
  const hec = form.kind === "hec";
  const set = (key) => (event) => {
    setConnection(null);
    probe.reset();
    setForm((prev) => ({
      ...prev,
      [key]: event.target.value,
      ...(key === "kind"
        ? { token: "", authValue: "", streams: ["audit_events"] }
        : {}),
    }));
  };
  const toggleStream = (value) =>
    setForm((prev) => ({
      ...prev,
      streams: prev.streams.includes(value)
        ? prev.streams.filter((s) => s !== value)
        : [...prev.streams, value],
    }));

  const save = async () => {
    const body = {
      name: form.name.trim(),
      url: form.url.trim(),
      streams: SIEM_STREAMS.map((s) => s.value).filter((v) =>
        form.streams.includes(v),
      ),
    };
    if (editing) body.id = destination.id;
    else body.kind = form.kind;
    if (hec && form.token) body.token = form.token;
    if (form.kind === "otlp_http") {
      body.auth_header = form.authHeader.trim();
      if (form.authValue) body.auth_value = form.authValue;
    }
    try {
      const result = await write.mutateAsync(body);
      setCreated(result);
      setForm(EMPTY);
      setConnection(null);
      setConfirming(false);
      probe.reset();
      onSaved?.();
    } catch {
      setConfirming(false);
    }
  };
  const busy = write.isPending || probe.isPending;
  const ready =
    form.name.trim() &&
    form.url.trim() &&
    form.streams.length > 0 &&
    (!hec || editing || form.token) &&
    !busy &&
    !disabled;

  return (
    <Box
      component="form"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) {
          if (editing) setConfirming(true);
          else save();
        }
      }}
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <TextField
        label="Name"
        size="small"
        value={form.name}
        onChange={set("name")}
        sx={FIELD_SX}
      />
      {!editing && (
        <TextField
          select
          label="Kind"
          size="small"
          value={form.kind}
          onChange={set("kind")}
          SelectProps={{ MenuProps: { disableScrollLock: true } }}
          sx={FIELD_SX}
        >
          {SIEM_KINDS.map((kind) => (
            <MenuItem key={kind.value} value={kind.value}>
              {kind.label}
            </MenuItem>
          ))}
        </TextField>
      )}
      <TextField
        label={
          hec
            ? "HEC URL"
            : form.kind === "otlp_http"
              ? "Collector base URL"
              : "Webhook URL"
        }
        size="small"
        value={form.url}
        onChange={set("url")}
        placeholder="https://"
        helperText={
          hec
            ? "The full event endpoint, including /services/collector/event."
            : form.kind === "otlp_http"
              ? "The gateway adds /v1/traces and /v1/logs."
              : "Each request is signed (Standard Webhooks)."
        }
        sx={FIELD_SX}
      />
      <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
        {SIEM_STREAMS.filter((s) => !hec || s.value === "audit_events").map(
          (stream) => (
            <FormControlLabel
              key={stream.value}
              control={
                <Checkbox
                  size="small"
                  checked={form.streams.includes(stream.value)}
                  onChange={() => toggleStream(stream.value)}
                />
              }
              label={<Typography typography="s2">{stream.label}</Typography>}
            />
          ),
        )}
      </Box>
      {hec && (
        <TextField
          label="HEC token"
          size="small"
          type="password"
          autoComplete="new-password"
          value={form.token}
          onChange={set("token")}
          sx={FIELD_SX}
          helperText={
            editing
              ? "Leave blank to keep the current token. Enter a token to test a connection or replace it."
              : "Write-only. Encrypted at rest and never shown again."
          }
        />
      )}
      {form.kind === "otlp_http" && (
        <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap" }}>
          <TextField
            label="Auth header"
            size="small"
            value={form.authHeader}
            onChange={set("authHeader")}
            placeholder="Authorization"
            sx={{ ...FIELD_SX, flex: "1 1 160px" }}
          />
          <TextField
            label="Auth header value"
            size="small"
            type="password"
            autoComplete="new-password"
            value={form.authValue}
            onChange={set("authValue")}
            helperText={
              editing ? "Leave blank to keep the current value." : "Write-only."
            }
            sx={{ ...FIELD_SX, flex: "2 1 220px" }}
          />
        </Box>
      )}
      {write.isError && (
        <Typography typography="s2" color="error.main" role="alert">
          {apiErrorMessage(write.error, "The destination was not saved.")}
        </Typography>
      )}
      {probe.isError && (
        <Typography typography="s2" color="error.main" role="alert">
          {apiErrorMessage(probe.error, "The connection test failed.")}
        </Typography>
      )}
      {connection && (
        <Typography typography="s2" role="status">
          {connection.message}
        </Typography>
      )}
      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        {hec && (
          <Button
            size="small"
            variant="outlined"
            disabled={busy || !form.url.trim() || !form.token}
            onClick={async () => {
              setConnection(null);
              try {
                setConnection(
                  await probe.mutateAsync({
                    url: form.url.trim(),
                    token: form.token,
                  }),
                );
              } catch {
                /* shown above */
              }
            }}
          >
            Test connection
          </Button>
        )}
        <Button
          type="submit"
          size="small"
          variant="contained"
          color="primary"
          disabled={!ready}
        >
          {editing ? "Save changes" : "Add destination"}
        </Button>
      </Box>
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={save}
        title="Confirm destination changes"
        message={`Save changes to ${destination?.name || "this destination"}? Future deliveries use the updated settings.`}
        confirmLabel="Save changes"
        confirmColor="primary"
        isLoading={write.isPending}
      />
      {created?.signing_secret && (
        <Box
          sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5 }}
        >
          <Typography
            typography="s2"
            color="text.secondary"
          >{`Signing secret for ${created.name}. Copy it now: it is not shown again.`}</Typography>
          <Typography
            typography="s2"
            sx={{ fontFamily: GATEWAY_MONO, overflowWrap: "anywhere" }}
          >
            {created.signing_secret}
          </Typography>
        </Box>
      )}
    </Box>
  );
}
SiemDestinationForm.propTypes = {
  disabled: PropTypes.bool,
  destination: PropTypes.object,
  onSaved: PropTypes.func,
};
