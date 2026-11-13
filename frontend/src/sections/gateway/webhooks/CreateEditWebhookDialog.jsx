import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Stack,
  FormControlLabel,
  Checkbox,
  Typography,
  IconButton,
  Box,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";

const EVENT_TYPES = [
  { value: "request.completed", label: "Request Completed" },
  { value: "guardrail.triggered", label: "Guardrail Triggered" },
  { value: "budget.exceeded", label: "Budget Exceeded" },
  { value: "error.occurred", label: "Error Occurred" },
  { value: "batch.completed", label: "Batch Completed" },
];

const EMPTY_FORM = {
  name: "",
  url: "",
  secret: "",
  events: [],
  description: "",
  headers: {},
};

const CreateEditWebhookDialog = ({
  open,
  onClose,
  onSubmit,
  webhook,
  isPending,
}) => {
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
        headers: webhook.headers || {},
      });
      const entries = Object.entries(webhook.headers || {});
      setHeaderEntries(
        entries.length > 0
          ? entries.map(([key, value]) => ({ key, value }))
          : [{ key: "", value: "" }],
      );
    } else {
      setForm(EMPTY_FORM);
      setHeaderEntries([{ key: "", value: "" }]);
    }
  }, [webhook, open]);

  const handleField = (field) => (e) =>
    setForm((f) => ({ ...f, [field]: e.target.value }));

  const handleEventToggle = (eventValue) => {
    setForm((f) => ({
      ...f,
      events: f.events.includes(eventValue)
        ? f.events.filter((e) => e !== eventValue)
        : [...f.events, eventValue],
    }));
  };

  const handleHeaderChange = (idx, field, value) => {
    setHeaderEntries((entries) =>
      entries.map((e, i) => (i === idx ? { ...e, [field]: value } : e)),
    );
  };

  const addHeaderRow = () =>
    setHeaderEntries((entries) => [...entries, { key: "", value: "" }]);

  const removeHeaderRow = (idx) =>
    setHeaderEntries((entries) => entries.filter((_, i) => i !== idx));

  const handleSubmit = () => {
    const headers = {};
    headerEntries.forEach(({ key, value }) => {
      if (key.trim()) headers[key.trim()] = value;
    });
    const payload = { ...form, headers };
    if (isEdit) payload.id = webhook.id;
    if (!payload.secret) delete payload.secret;
    onSubmit(payload);
  };

  const isValid = form.name.trim() && form.url.trim() && form.events.length > 0;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle>{isEdit ? "Edit Webhook" : "Create Webhook"}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <TextField
            label="Name"
            value={form.name}
            onChange={handleField("name")}
            fullWidth
            required
          />
          <TextField
            label="URL"
            value={form.url}
            onChange={handleField("url")}
            fullWidth
            required
            placeholder="https://example.com/webhook"
          />
          <TextField
            label="Secret"
            value={form.secret}
            onChange={handleField("secret")}
            fullWidth
            placeholder={
              isEdit ? "Leave blank to keep existing" : "Optional HMAC secret"
            }
            type="password"
          />
          <TextField
            label="Description"
            value={form.description}
            onChange={handleField("description")}
            fullWidth
            multiline
            rows={2}
          />

          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Event Subscriptions
            </Typography>
            {EVENT_TYPES.map((evt) => (
              <FormControlLabel
                key={evt.value}
                control={
                  <Checkbox
                    checked={form.events.includes(evt.value)}
                    onChange={() => handleEventToggle(evt.value)}
                    size="small"
                  />
                }
                label={evt.label}
              />
            ))}
          </Box>

          <Box>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              sx={{ mb: 1 }}
            >
              <Typography variant="subtitle2">Custom Headers</Typography>
              <Button
                size="small"
                startIcon={<Iconify icon="solar:add-linear" width={16} />}
                onClick={addHeaderRow}
              >
                Add
              </Button>
            </Stack>
            {headerEntries.map((entry, idx) => (
              <Stack key={idx} direction="row" spacing={1} sx={{ mb: 1 }}>
                <TextField
                  size="small"
                  placeholder="Header name"
                  value={entry.key}
                  onChange={(e) =>
                    handleHeaderChange(idx, "key", e.target.value)
                  }
                  sx={{ flex: 1 }}
                />
                <TextField
                  size="small"
                  placeholder="Header value"
                  value={entry.value}
                  onChange={(e) =>
                    handleHeaderChange(idx, "value", e.target.value)
                  }
                  sx={{ flex: 1 }}
                />
                <IconButton
                  size="small"
                  onClick={() => removeHeaderRow(idx)}
                  disabled={headerEntries.length === 1}
                >
                  <Iconify icon="solar:close-linear" width={20} />
                </IconButton>
              </Stack>
            ))}
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={!isValid || isPending}
        >
          {isPending ? "Saving..." : isEdit ? "Update" : "Create"}
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
