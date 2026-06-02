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

    const payload = {
      name: name.trim(),
      recipients,
      events,
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
          </Box>

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
            helperText="Minimum time between repeated alerts"
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
