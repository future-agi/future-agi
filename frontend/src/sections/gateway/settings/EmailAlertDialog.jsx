import { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import Iconify from "src/components/iconify";
import {
  useCreateEmailAlert,
  useUpdateEmailAlert,
  useTestEmailAlert,
} from "./hooks/useEmailAlerts";

const EVENT_OPTIONS = [
  { value: "budget.exceeded", label: "Budget Exceeded" },
  { value: "error.occurred", label: "Error Occurred" },
  { value: "error.rate_spike", label: "Error Rate Spike" },
  { value: "guardrail.triggered", label: "Guardrail Triggered" },
  { value: "latency.spike", label: "Latency Spike" },
  { value: "cost.threshold", label: "Cost Threshold" },
];

// Request events are sent when the gateway's request log arrives; threshold
// events are evaluated every minute from the org's request logs.
const THRESHOLD_EVENTS = [
  "error.rate_spike",
  "latency.spike",
  "cost.threshold",
];

// The backend's defaults (agentcc/services/alert_dispatcher.py DEFAULT_THRESHOLDS).
const THRESHOLD_DEFAULTS = {
  error_rate_percent: 5,
  latency_ms: 5000,
  cost_usd: 10,
  window_minutes: 5,
  min_requests: 5,
};

const THRESHOLD_FIELDS = [
  {
    key: "error_rate_percent",
    label: "Error rate (%)",
    events: ["error.rate_spike"],
    helperText: "Share of failed requests in the window",
    check: (v) => v > 0 && v <= 100,
    error: "Error rate must be above 0 and at most 100.",
  },
  {
    key: "latency_ms",
    label: "p95 latency (ms)",
    events: ["latency.spike"],
    helperText: "95th percentile latency in the window",
    check: (v) => Number.isInteger(v) && v >= 1,
    error: "p95 latency must be a whole number of milliseconds, at least 1.",
  },
  {
    key: "cost_usd",
    label: "Spend (USD)",
    events: ["cost.threshold"],
    helperText: "Total spend in the window",
    check: (v) => v > 0,
    error: "Spend must be above 0.",
  },
  {
    key: "window_minutes",
    label: "Window (minutes)",
    events: THRESHOLD_EVENTS,
    helperText: "How far back each check looks (1 to 1440)",
    check: (v) => Number.isInteger(v) && v >= 1 && v <= 1440,
    error: "Window must be a whole number of minutes from 1 to 1440.",
  },
  {
    key: "min_requests",
    label: "Minimum requests",
    events: ["error.rate_spike", "latency.spike"],
    helperText:
      "Rate and latency are checked only with at least this many requests",
    check: (v) => Number.isInteger(v) && v >= 1,
    error: "Minimum requests must be a whole number, at least 1.",
  },
];

const thresholdState = (stored) => {
  const values = { ...THRESHOLD_DEFAULTS, ...(stored || {}) };
  return Object.fromEntries(
    THRESHOLD_FIELDS.map((f) => [f.key, String(values[f.key])]),
  );
};

const PROVIDER_OPTIONS = [
  { value: "sendgrid", label: "SendGrid" },
  { value: "resend", label: "Resend" },
  { value: "smtp", label: "SMTP" },
];

export default function EmailAlertDialog({ open, onClose, alert }) {
  const isEdit = Boolean(alert);

  const [name, setName] = useState(alert?.name || "");
  const [recipients, setRecipients] = useState(alert?.recipients || []);
  const [events, setEvents] = useState(alert?.events || []);
  const [provider, setProvider] = useState(alert?.provider || "sendgrid");
  const [cooldownMinutes, setCooldownMinutes] = useState(
    alert?.cooldownMinutes ?? alert?.cooldown_minutes ?? 5,
  );
  const [isActive, setIsActive] = useState(alert?.is_active !== false);
  const [thresholds, setThresholds] = useState(() =>
    thresholdState(alert?.thresholds),
  );

  // Provider config
  const [apiKey, setApiKey] = useState("");
  const [fromEmail, setFromEmail] = useState("");
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState(587);
  const [smtpUsername, setSmtpUsername] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");

  const [error, setError] = useState(null);
  const [testResult, setTestResult] = useState(null);

  const { mutate: createAlert, isPending: isCreating } = useCreateEmailAlert();
  const { mutate: updateAlert, isPending: isUpdating } = useUpdateEmailAlert();
  const { mutate: testAlert, isPending: isTesting } = useTestEmailAlert();

  useEffect(() => {
    if (alert) {
      setName(alert.name || "");
      setRecipients(alert.recipients || []);
      setEvents(alert.events || []);
      setProvider(alert.provider || "sendgrid");
      setCooldownMinutes(alert.cooldownMinutes ?? alert.cooldown_minutes ?? 5);
      setIsActive(alert.is_active !== false);
      setThresholds(thresholdState(alert.thresholds));
      // Provider config is masked, so we don't pre-fill sensitive fields
      const cfg = alert.providerConfig || alert.provider_config || {};
      setFromEmail(cfg.from_email || "");
      if (alert.provider === "smtp") {
        setSmtpHost(cfg.host || "");
        setSmtpPort(cfg.port || 587);
        setSmtpUsername(cfg.username || "");
      }
    } else {
      setName("");
      setRecipients([]);
      setEvents([]);
      setProvider("sendgrid");
      setCooldownMinutes(5);
      setIsActive(true);
      setThresholds(thresholdState());
      setApiKey("");
      setFromEmail("");
      setSmtpHost("");
      setSmtpPort(587);
      setSmtpUsername("");
      setSmtpPassword("");
    }
    setError(null);
    setTestResult(null);
  }, [alert, open]);

  const shownThresholds = THRESHOLD_FIELDS.filter((field) =>
    field.events.some((event) => events.includes(event)),
  );

  const setThreshold = (key, value) =>
    setThresholds((prev) => ({ ...prev, [key]: value }));

  const buildProviderConfig = () => {
    if (provider === "sendgrid" || provider === "resend") {
      const config = { from_email: fromEmail };
      if (apiKey) config.api_key = apiKey;
      return config;
    }
    if (provider === "smtp") {
      const config = {
        host: smtpHost,
        port: smtpPort,
        from_email: fromEmail,
        use_tls: true,
      };
      if (smtpUsername) config.username = smtpUsername;
      if (smtpPassword) config.password = smtpPassword;
      return config;
    }
    return {};
  };

  const handleSave = () => {
    setError(null);
    if (!name.trim()) {
      setError("Alert name is required.");
      return;
    }
    if (recipients.length === 0) {
      setError("At least one recipient is required.");
      return;
    }
    if (events.length === 0) {
      setError("Select at least one event type.");
      return;
    }

    // Stored keys the dialog does not show are kept as they are, except a
    // threshold value the backend would refuse (it is never evaluated).
    const thresholdPayload = {};
    for (const [key, value] of Object.entries(alert?.thresholds || {})) {
      const field = THRESHOLD_FIELDS.find((f) => f.key === key);
      if (!field || (typeof value === "number" && field.check(value))) {
        thresholdPayload[key] = value;
      }
    }
    for (const field of shownThresholds) {
      const value = Number(thresholds[field.key]);
      if (thresholds[field.key] === "" || !field.check(value)) {
        setError(field.error);
        return;
      }
      thresholdPayload[field.key] = value;
    }

    const payload = {
      name: name.trim(),
      recipients,
      events,
      thresholds: thresholdPayload,
      provider,
      provider_config: buildProviderConfig(),
      cooldown_minutes: cooldownMinutes,
      is_active: isActive,
    };

    const handler = {
      onSuccess: () => onClose(),
      onError: (err) => {
        setError(
          err?.response?.data?.message ||
            err?.message ||
            "Failed to save alert.",
        );
      },
    };

    if (isEdit) {
      updateAlert({ id: alert.id, ...payload }, handler);
    } else {
      createAlert(payload, handler);
    }
  };

  const handleTest = () => {
    if (!isEdit) return;
    setTestResult(null);
    testAlert(
      { id: alert.id },
      {
        onSuccess: (data) => {
          setTestResult({
            severity: data?.status ? "success" : "error",
            message:
              data?.result?.message || data?.message || "Test completed.",
          });
        },
        onError: (err) => {
          setTestResult({
            severity: "error",
            message: err?.response?.data?.message || "Test failed.",
          });
        },
      },
    );
  };

  const toggleEvent = (eventValue) => {
    setEvents((prev) =>
      prev.includes(eventValue)
        ? prev.filter((e) => e !== eventValue)
        : [...prev, eventValue],
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography fontWeight={600}>
            {isEdit ? "Edit Email Alert" : "New Email Alert"}
          </Typography>
          <IconButton onClick={onClose} size="small">
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent dividers>
        <Stack spacing={2.5} sx={{ pt: 1 }}>
          <TextField
            label="Alert Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            size="small"
            fullWidth
            required
            placeholder="e.g. Production Error Alerts"
          />

          <Autocomplete
            multiple
            freeSolo
            options={[]}
            value={recipients}
            onChange={(_, newValue) => setRecipients(newValue)}
            renderTags={(value, getTagProps) =>
              value.map((option, index) => (
                <Chip
                  label={option}
                  size="small"
                  {...getTagProps({ index })}
                  key={option}
                />
              ))
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Recipients"
                size="small"
                placeholder="Type email and press Enter"
                helperText="Press Enter after each email address"
              />
            )}
          />

          <Box>
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 500,
                mb: 1,
                color: "text.primary",
              }}
            >
              Alert Events
            </Typography>
            <Stack spacing={0.5}>
              {EVENT_OPTIONS.map((opt) => (
                <FormControlLabel
                  key={opt.value}
                  control={
                    <Checkbox
                      size="small"
                      checked={events.includes(opt.value)}
                      onChange={() => toggleEvent(opt.value)}
                    />
                  }
                  label={
                    <Typography sx={{ typography: "s2" }}>
                      {opt.label}
                    </Typography>
                  }
                />
              ))}
            </Stack>
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
            >
              Error Occurred, Guardrail Triggered and Budget Exceeded are sent
              when the gateway logs the request; Budget Exceeded is sent once
              per budget level per budget period, or, when the gateway does not
              name the level, once per error code per UTC day.
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              Error Rate Spike, Latency Spike and Cost Threshold are checked
              every minute against your gateway&apos;s request logs, and sent
              again after each cooldown while the threshold is still crossed.
            </Typography>
          </Box>

          {shownThresholds.length > 0 && (
            <Box>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 500,
                  mb: 1,
                  color: "text.primary",
                }}
              >
                Thresholds
              </Typography>
              <Stack spacing={2}>
                {shownThresholds.map((field) => (
                  <TextField
                    key={field.key}
                    label={field.label}
                    value={thresholds[field.key]}
                    onChange={(e) => setThreshold(field.key, e.target.value)}
                    size="small"
                    fullWidth
                    type="number"
                    helperText={field.helperText}
                  />
                ))}
              </Stack>
            </Box>
          )}

          <TextField
            select
            label="Email Provider"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            size="small"
            fullWidth
          >
            {PROVIDER_OPTIONS.map((p) => (
              <MenuItem key={p.value} value={p.value}>
                {p.label}
              </MenuItem>
            ))}
          </TextField>

          {/* Provider-specific config */}
          {(provider === "sendgrid" || provider === "resend") && (
            <Stack spacing={2}>
              <TextField
                label="API Key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                size="small"
                fullWidth
                type="password"
                placeholder={isEdit ? "Leave empty to keep current" : ""}
              />
              <TextField
                label="From Email"
                value={fromEmail}
                onChange={(e) => setFromEmail(e.target.value)}
                size="small"
                fullWidth
                placeholder="alerts@yourdomain.com"
              />
            </Stack>
          )}

          {provider === "smtp" && (
            <Stack spacing={2}>
              <TextField
                label="SMTP Host"
                value={smtpHost}
                onChange={(e) => setSmtpHost(e.target.value)}
                size="small"
                fullWidth
                required
                placeholder="smtp.gmail.com"
              />
              <TextField
                label="SMTP Port"
                value={smtpPort}
                onChange={(e) => setSmtpPort(Number(e.target.value))}
                size="small"
                fullWidth
                type="number"
              />
              <TextField
                label="Username"
                value={smtpUsername}
                onChange={(e) => setSmtpUsername(e.target.value)}
                size="small"
                fullWidth
              />
              <TextField
                label="Password"
                value={smtpPassword}
                onChange={(e) => setSmtpPassword(e.target.value)}
                size="small"
                fullWidth
                type="password"
                placeholder={isEdit ? "Leave empty to keep current" : ""}
              />
              <TextField
                label="From Email"
                value={fromEmail}
                onChange={(e) => setFromEmail(e.target.value)}
                size="small"
                fullWidth
                placeholder="alerts@yourdomain.com"
              />
            </Stack>
          )}

          <TextField
            label="Cooldown (minutes)"
            value={cooldownMinutes}
            onChange={(e) => setCooldownMinutes(Number(e.target.value))}
            size="small"
            fullWidth
            type="number"
            helperText="At most one email per alert every cooldown period; events in between are sent together in the next email."
          />

          <FormControlLabel
            control={
              <Checkbox
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
                size="small"
              />
            }
            label={
              <Typography sx={{ typography: "s2" }}>Alert is active</Typography>
            }
          />

          {error && <Alert severity="error">{error}</Alert>}
          {testResult && (
            <Alert severity={testResult.severity}>{testResult.message}</Alert>
          )}
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 2 }}>
        {isEdit && (
          <LoadingButton
            variant="outlined"
            size="small"
            loading={isTesting}
            onClick={handleTest}
            startIcon={<Iconify icon="mdi:email-outline" />}
            sx={{ mr: "auto" }}
          >
            Send Test
          </LoadingButton>
        )}
        <Button variant="outlined" size="small" onClick={onClose}>
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          size="small"
          loading={isCreating || isUpdating}
          onClick={handleSave}
          sx={{ fontWeight: 500 }}
        >
          {isEdit ? "Update" : "Create"}
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

EmailAlertDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  alert: PropTypes.object,
};
