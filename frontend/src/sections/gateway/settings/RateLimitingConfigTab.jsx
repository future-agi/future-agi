/* eslint-disable react/prop-types */
import React from "react";
import {
  Box,
  Stack,
  Typography,
  TextField,
  Switch,
  FormControlLabel,
  Paper,
  Divider,
  Alert,
} from "@mui/material";
import { Icon } from "@iconify/react";

const RateLimitingConfigTab = ({ rateLimiting, onChange }) => {
  const config = rateLimiting || {};

  const update = (key, value) => {
    onChange({ ...config, [key]: value });
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="solar:speedometer-middle-linear" width={24} />
        <Typography variant="h6">Rate Limiting</Typography>
      </Stack>

      <Alert severity="info" sx={{ mb: 2 }}>
        Configure per-organization rate limits. These override the gateway-level
        defaults for this organization&apos;s API keys.
      </Alert>

      <FormControlLabel
        control={
          <Switch
            checked={config.enabled || false}
            onChange={(e) => update("enabled", e.target.checked)}
          />
        }
        label="Enable per-org rate limiting"
        sx={{ mb: 2 }}
      />

      {config.enabled && (
        <Stack spacing={3}>
          {/* Global Limits */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight="600" gutterBottom>
              Organization-Wide Limits
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Maximum requests/tokens per minute across all keys in this
              organization.
            </Typography>
            <Stack direction="row" spacing={2}>
              <TextField
                label="Requests per Minute (RPM)"
                type="number"
                size="small"
                fullWidth
                value={config.global_rpm || ""}
                onChange={(e) =>
                  update("global_rpm", Number(e.target.value) || 0)
                }
                helperText="0 = unlimited"
              />
              <TextField
                label="Tokens per Minute (TPM)"
                type="number"
                size="small"
                fullWidth
                value={config.global_tpm || ""}
                onChange={(e) =>
                  update("global_tpm", Number(e.target.value) || 0)
                }
                helperText="0 = unlimited"
              />
            </Stack>
          </Paper>

          <Divider />

          {/* Per-Key Limits */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight="600" gutterBottom>
              Per-Key Limits
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Maximum requests/tokens per minute for each individual API key.
            </Typography>
            <Stack direction="row" spacing={2}>
              <TextField
                label="RPM per Key"
                type="number"
                size="small"
                fullWidth
                value={config.per_key_rpm || ""}
                onChange={(e) =>
                  update("per_key_rpm", Number(e.target.value) || 0)
                }
                helperText="0 = unlimited"
              />
              <TextField
                label="TPM per Key"
                type="number"
                size="small"
                fullWidth
                value={config.per_key_tpm || ""}
                onChange={(e) =>
                  update("per_key_tpm", Number(e.target.value) || 0)
                }
                helperText="0 = unlimited"
              />
            </Stack>
          </Paper>

          <Divider />

          {/* Per-Model and Per-User Limits */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight="600" gutterBottom>
              Additional Limits
            </Typography>
            <Stack direction="row" spacing={2}>
              <TextField
                label="RPM per Model"
                type="number"
                size="small"
                fullWidth
                value={config.per_model_rpm || ""}
                onChange={(e) =>
                  update("per_model_rpm", Number(e.target.value) || 0)
                }
                helperText="Per-model limit across all keys"
              />
              <TextField
                label="RPM per User"
                type="number"
                size="small"
                fullWidth
                value={config.per_user_rpm || ""}
                onChange={(e) =>
                  update("per_user_rpm", Number(e.target.value) || 0)
                }
                helperText="Per x-agentcc-user-id limit"
              />
            </Stack>
          </Paper>
        </Stack>
      )}
    </Box>
  );
};

export default RateLimitingConfigTab;
