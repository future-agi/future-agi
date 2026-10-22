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
  Alert,
} from "@mui/material";
import { Icon } from "@iconify/react";
import { useGatewayContext } from "../context/useGatewayContext";
import { useGatewayConfig } from "../providers/hooks/useGatewayConfig";

// Whether this gateway applies the org's rate limits: GET /-/config's
// org_sections_enforced lists rate_limiting only when the
// gateway's own rate_limiting switch is on. Absent: unknown.
const EnforcementState = () => {
  const { gatewayId } = useGatewayContext();
  const { data, isLoading } = useGatewayConfig(gatewayId);
  if (isLoading) return null;

  const sections = data?.org_sections_enforced;
  if (!Array.isArray(sections)) {
    return (
      <Alert severity="info" sx={{ mb: 2 }}>
        Enforcement unknown: this gateway does not report which org config
        sections it enforces.
      </Alert>
    );
  }
  if (!sections.includes("rate_limiting")) {
    return (
      <Alert severity="error" sx={{ mb: 2 }}>
        Not enforced on this gateway: its rate limiting is switched off, so
        these limits are stored but no request is checked against them.
      </Alert>
    );
  }
  return (
    <Alert severity="success" sx={{ mb: 2 }}>
      This gateway enforces these limits.
    </Alert>
  );
};

// The routes whose handler applies the org's rate_limiting section; the
// gateway's limits_routes_test.go proves each, and every route the scope
// note names as not counted (limits_speech_test.go the speech relays).
const COUNTED = "chat, completions, Responses, Messages and Gemini requests";

const LimitField = ({ label, field, config, update, help }) => (
  <TextField
    label={label}
    type="number"
    size="small"
    fullWidth
    value={config[field] || ""}
    onChange={(e) => update(field, Number(e.target.value) || 0)}
    inputProps={{ min: 0 }}
    helperText={`${help} 0 = unlimited.`}
  />
);

const RateLimitingConfigTab = ({ rateLimiting, onChange }) => {
  const config = rateLimiting || {};

  const update = (key, value) => {
    onChange({ ...config, [key]: value });
  };

  const field = { config, update };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="mdi:speedometer" width={24} />
        <Typography variant="h6">Rate Limiting</Typography>
      </Stack>

      <EnforcementState />

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
        <Stack spacing={2}>
          <Typography variant="body2" color="text.secondary">
            These limits count chat completions, completions, Responses,
            Anthropic Messages and Gemini (/v1beta) requests. Each relayed
            Deepgram or Cartesia speech session counts as one request and adds
            no tokens. A session counts per model only when its URL names the
            model, and a new session is refused while a token limit that holds
            it is spent for the minute. Embeddings (/v1/embeddings), images,
            audio (/v1/audio), rerank, search, OCR, Assistants, video, realtime
            and scheduled requests are not counted or limited by this section.
          </Typography>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight="bold" sx={{ mb: 2 }}>
              Organization-Wide Limits
            </Typography>
            <Stack spacing={2}>
              <LimitField
                {...field}
                label="Requests per Minute (RPM)"
                field="global_rpm"
                help={`This organization's ${COUNTED}, from every key, counted together.`}
              />
              <LimitField
                {...field}
                label="Tokens per Minute (TPM)"
                field="global_tpm"
                help={`Tokens (prompt and completion) of this organization's ${COUNTED}, counted together.`}
              />
            </Stack>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight="bold" sx={{ mb: 2 }}>
              Per-Key Limits
            </Typography>
            <Stack spacing={2}>
              <LimitField
                {...field}
                label="RPM per Key"
                field="per_key_rpm"
                help={`Each API key's ${COUNTED}, counted separately. A limit set on the key itself is used instead.`}
              />
              <LimitField
                {...field}
                label="TPM per Key"
                field="per_key_tpm"
                help={`Each API key's tokens on ${COUNTED}, counted separately.`}
              />
            </Stack>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight="bold" sx={{ mb: 2 }}>
              Per-Model and Per-User Limits
            </Typography>
            <Stack spacing={2}>
              <LimitField
                {...field}
                label="RPM per Model"
                field="per_model_rpm"
                help={`Each model's ${COUNTED} from every key of the organization, counted separately by the model name the request sends.`}
              />
              <LimitField
                {...field}
                label="RPM per User"
                field="per_user_rpm"
                help="Each end user's requests from every key of the organization, counted separately. On chat completions the user is x-agentcc-user-id (or metadata.agent.user_id), else the body's user field; on completions and Responses it is the body's user field. Anthropic Messages and Gemini requests, and a request that names no user, are not limited by this field."
              />
            </Stack>
          </Paper>

          <Typography variant="body2" color="text.secondary">
            Limits count per minute; gateway replicas share the counts when the
            gateway runs with Redis. A request is let through while this
            minute&apos;s tokens are under the limit, and its tokens count when
            it finishes, so one request can take the minute over the limit. A
            refused request gets 429: on chat completions, completions and
            Responses it is rate_limit_exceeded naming the field that refused
            it, with Retry-After; on Anthropic Messages and Gemini it is that
            API&apos;s own error, whose message names the field and the seconds
            to wait. An answer served from the response cache is not counted
            against, or refused by, any rate limit.
          </Typography>
        </Stack>
      )}
    </Box>
  );
};

export default RateLimitingConfigTab;
