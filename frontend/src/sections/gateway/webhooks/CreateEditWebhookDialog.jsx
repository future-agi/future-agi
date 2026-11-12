import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import SvgColor from "src/components/svg-color";
import { Glyph } from "../settings/ui";

import RecordedOnly from "../settings/ui/RecordedOnly";
import {
  WEBHOOK_EVENTS,
  offeredWebhookEvents,
  onlyUnproduced,
} from "./webhookEvents";

const HTTP_WARNING =
  "This URL uses HTTP, so deliveries are not encrypted. Webhook payloads can include event data from your organization. Use an HTTPS URL to protect payloads in transit.";

const EMPTY_FORM = {
  name: "",
  url: "",
  secret: "",
  events: [],
  description: "",
  is_active: true,
};

// A new webhook offers every event the gateway produces, including guardrail
// events while the node pipeline is off. Their description states when they
// fire. Opening or saving a webhook never changes its existing subscriptions.

function urlError(url) {
  const value = url.trim();
  if (!value) return "";
  try {
    const parsed = new URL(value);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") return "";
  } catch {
    // fall through
  }
  return "Start with https:// (or http://) and a host name.";
}

const CreateEditWebhookDialog = ({
  open,
  onClose,
  onSubmit,
  webhook,
  isPending,
}) => {
  const offered = offeredWebhookEvents();
  const [form, setForm] = useState(EMPTY_FORM);
  const [headerEntries, setHeaderEntries] = useState([{ key: "", value: "" }]);
  const isEdit = Boolean(webhook);

  useEffect(() => {
    if (webhook) {
      setForm({
        name: webhook.name || "",
        url: webhook.url || "",
        secret: "",
        events: webhook.events || [],
        description: webhook.description || "",
        is_active: webhook.is_active !== false,
      });
      const entries = Object.entries(webhook.headers || {});
      setHeaderEntries(
        entries.length
          ? entries.map(([key, value]) => ({ key, value }))
          : [{ key: "", value: "" }],
      );
    } else {
      setForm(EMPTY_FORM);
      setHeaderEntries([{ key: "", value: "" }]);
    }
  }, [webhook, open]);

  const field = (name) => (event) =>
    setForm((f) => ({ ...f, [name]: event.target.value }));

  const toggleEvent = (value) =>
    setForm((f) => ({
      ...f,
      events: f.events.includes(value)
        ? f.events.filter((e) => e !== value)
        : [...f.events, value],
    }));

  const setHeader = (idx, key, value) =>
    setHeaderEntries((entries) =>
      entries.map((e, i) => (i === idx ? { ...e, [key]: value } : e)),
    );

  const submit = () => {
    const headers = {};
    headerEntries.forEach(({ key, value }) => {
      if (key.trim()) headers[key.trim()] = value;
    });
    const payload = {
      ...form,
      name: form.name.trim(),
      url: form.url.trim(),
      headers,
    };
    if (isEdit) payload.id = webhook.id;
    if (!payload.secret) delete payload.secret;
    onSubmit(payload);
  };

  const storedUnsent = WEBHOOK_EVENTS.filter(
    (e) => !e.produced && form.events.includes(e.value),
  );
  const badUrl = urlError(form.url);
  const httpWarning = !badUrl && /^http:/i.test(form.url.trim());
  const neverCalled = onlyUnproduced(form.events);
  const isValid =
    form.name.trim() && form.url.trim() && !badUrl && form.events.length > 0;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>{isEdit ? "Edit webhook" : "Create webhook"}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <TextField
            label="Name"
            value={form.name}
            onChange={field("name")}
            fullWidth
            required
            size="small"
          />
          <TextField
            label="URL"
            value={form.url}
            onChange={field("url")}
            fullWidth
            required
            size="small"
            placeholder="https://example.com/webhooks/gateway"
            error={Boolean(badUrl)}
            helperText={
              badUrl ||
              (httpWarning
                ? HTTP_WARNING
                : "The gateway POSTs each event here as JSON.")
            }
            FormHelperTextProps={
              httpWarning ? { sx: { color: "warning.main" } } : undefined
            }
          />
          <TextField
            label="Secret"
            value={form.secret}
            onChange={field("secret")}
            fullWidth
            size="small"
            type="password"
            autoComplete="new-password"
            placeholder={isEdit ? "Leave blank to keep the current secret" : ""}
            helperText="With a secret, every delivery carries X-Agentcc-Signature: sha256=<HMAC-SHA256 of the body>, so your endpoint can check it came from this gateway."
          />
          <TextField
            label="Description"
            value={form.description}
            onChange={field("description")}
            fullWidth
            size="small"
            multiline
            rows={2}
          />

          <Box>
            <Typography typography="s1" fontWeight={500} sx={{ mb: 0.5 }}>
              Events
            </Typography>
            <Stack>
              {offered.map((event) => (
                <FormControlLabel
                  key={event.value}
                  control={
                    <Checkbox
                      size="small"
                      checked={form.events.includes(event.value)}
                      onChange={() => toggleEvent(event.value)}
                    />
                  }
                  label={
                    <Typography typography="s1" component="span">
                      {event.label}{" "}
                      <Typography
                        component="span"
                        typography="s2"
                        color="text.secondary"
                      >
                        {event.value} · {event.description}
                      </Typography>
                    </Typography>
                  }
                />
              ))}
            </Stack>
            <RecordedOnly
              ids={storedUnsent.map((e) => e.note)}
              rows={storedUnsent.map((e) => ({
                key: e.value,
                label: e.label,
                value: e.value,
                mono: true,
              }))}
              title="Also subscribed"
              description="Stored on this webhook. The gateway never sends these events."
              testId="webhook-unsent-events"
              sx={{ mt: 1 }}
            />
            {neverCalled && (
              <Typography
                typography="s2"
                color="warning.main"
                sx={{ mt: 1 }}
                role="status"
              >
                With only these events this webhook is never called: add an
                event the gateway sends.
              </Typography>
            )}
          </Box>

          <Box>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 1 }}
            >
              <Box>
                <Typography typography="s1" fontWeight={500}>
                  Headers
                </Typography>
                <Typography typography="s2" color="text.secondary">
                  Sent with every delivery, for example an authorization header
                  your endpoint expects.
                </Typography>
              </Box>
              <Button
                size="small"
                startIcon={<Glyph name="add" />}
                sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                onClick={() =>
                  setHeaderEntries((entries) => [
                    ...entries,
                    { key: "", value: "" },
                  ])
                }
              >
                Add header
              </Button>
            </Stack>
            {headerEntries.map((entry, idx) => (
              <Stack key={idx} direction="row" spacing={1} sx={{ mb: 1 }}>
                <TextField
                  size="small"
                  placeholder="Header name"
                  value={entry.key}
                  onChange={(e) => setHeader(idx, "key", e.target.value)}
                  inputProps={{ "aria-label": `Header ${idx + 1} name` }}
                  sx={{ flex: 1 }}
                />
                <TextField
                  size="small"
                  placeholder="Value"
                  value={entry.value}
                  onChange={(e) => setHeader(idx, "value", e.target.value)}
                  inputProps={{ "aria-label": `Header ${idx + 1} value` }}
                  sx={{ flex: 1 }}
                />
                <IconButton
                  size="small"
                  aria-label={`Remove header ${idx + 1}`}
                  onClick={() =>
                    setHeaderEntries((entries) =>
                      entries.filter((_, i) => i !== idx),
                    )
                  }
                  disabled={headerEntries.length === 1}
                >
                  <SvgColor
                    src="/assets/icons/ic_delete.svg"
                    aria-hidden
                    sx={{ width: 16, height: 16 }}
                  />
                </IconButton>
              </Stack>
            ))}
          </Box>

          {isEdit && (
            <FormControlLabel
              control={
                <Switch
                  checked={form.is_active}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, is_active: e.target.checked }))
                  }
                />
              }
              label={
                <Typography typography="s1" component="span">
                  Active
                </Typography>
              }
            />
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          color="primary"
          onClick={submit}
          disabled={!isValid || isPending}
        >
          {isPending ? "Saving…" : isEdit ? "Save" : "Create"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateEditWebhookDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSubmit: PropTypes.func.isRequired,
  webhook: PropTypes.object,
  isPending: PropTypes.bool,
};

export default CreateEditWebhookDialog;
