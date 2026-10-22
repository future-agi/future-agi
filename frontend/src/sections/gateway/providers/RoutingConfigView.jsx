import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Card,
  CardContent,
  Typography,
  Box,
  Stack,
  Chip,
  Button,
  TextField,
  MenuItem,
  Switch,
  Autocomplete,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { useUpdateConfig } from "./hooks/useGatewayConfig";
import {
  NO_ROUTING_STRATEGY,
  retryOnTimeout,
  ROUTING_STRATEGY_CHOICES,
  routingStrategyChoice,
} from "../fallbacks/fallbackSwitches";
import { useOrgConfig, useCreateOrgConfig } from "./hooks/useOrgConfig";
import OrgConfigEditor from "../../gateway/settings/OrgConfigEditor";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";

// Status codes either list can match (a list matches the
// upstream's status or the gateway's).
const STATUS_CODE_OPTIONS = ["429", "500", "502", "503", "504"];

// A status-code list: chips, typed codes, and a sentence saying what it
// drives.
const StatusCodeList = ({
  label,
  helper,
  value,
  onChange,
  disabled,
  testId,
}) => (
  <Box data-testid={testId}>
    <Autocomplete
      multiple
      freeSolo
      options={STATUS_CODE_OPTIONS}
      value={value}
      onChange={(_, val) => onChange(val)}
      disabled={disabled}
      renderTags={(tags, getTagProps) =>
        tags.map((option, index) => (
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
          label={label}
          placeholder="Type code + Enter"
          size="small"
          helperText={helper}
        />
      )}
    />
  </Box>
);

StatusCodeList.propTypes = {
  label: PropTypes.string.isRequired,
  helper: PropTypes.string.isRequired,
  value: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  testId: PropTypes.string,
};

const codesOf = (section) =>
  (section?.on_status_codes ?? section?.onStatusCodes ?? []).map(String);

const RoutingConfigView = ({ config, gatewayId }) => {
  const routing = config?.routing || {};
  const failover = routing?.failover || {};
  const retry = routing?.retry || {};
  const rateLimit = config?.rate_limiting ?? config?.rateLimiting ?? {};

  const [strategy, setStrategy] = useState(NO_ROUTING_STRATEGY);
  const [strategyDirty, setStrategyDirty] = useState(false);
  const [failoverEnabled, setFailoverEnabled] = useState(false);
  const [maxAttempts, setMaxAttempts] = useState("3");
  const [failoverCodes, setFailoverCodes] = useState([]);
  const [retryCodes, setRetryCodes] = useState([]);
  const [retryTimeout, setRetryTimeout] = useState(false);
  const [retryDirty, setRetryDirty] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);

  const updateConfig = useUpdateConfig();
  const { canEdit } = useGatewayAdmin();
  const { data: activeConfig } = useOrgConfig();
  const createMutation = useCreateOrgConfig();

  const handleEditorSave = (configData) => {
    createMutation.mutate(configData, {
      onSuccess: () => setEditorOpen(false),
    });
  };

  useEffect(() => {
    setStrategy(routingStrategyChoice(routing));
    setStrategyDirty(false);
    setFailoverEnabled(failover?.enabled ?? false);
    setMaxAttempts(
      String(failover?.max_attempts ?? failover?.maxAttempts ?? 3),
    );
    setFailoverCodes(codesOf(failover));
    setRetryCodes(codesOf(retry));
    setRetryTimeout(retryOnTimeout(routing));
    setRetryDirty(false);
    setDirty(false);
  }, [config]);

  const handleChange = (setter) => (value) => {
    setter(value);
    setDirty(true);
  };

  const handleRetryChange = (setter) => (value) => {
    setter(value);
    setRetryDirty(true);
    setDirty(true);
  };

  const handleStrategyChange = (value) => {
    setStrategy(value);
    setStrategyDirty(true);
    setDirty(true);
  };

  const handleSave = () => {
    const routingPatch = {
      routing: {
        failover: {
          enabled: failoverEnabled,
          max_attempts: Number(maxAttempts),
          on_status_codes: failoverCodes.map(Number),
        },
      },
    };
    // Sent only when the strategy changed, so a save of the other fields
    // never sets one; None sends null, which update-config's merge patch
    // reads as "delete the stored strategy".
    if (strategyDirty) {
      routingPatch.routing.strategy =
        strategy === NO_ROUTING_STRATEGY ? null : strategy;
    }
    // Sent only when a retry field changed. update-config deep-merges the
    // patch into the stored config, so retry.enabled and the backoff fields
    // stay as stored without being sent here.
    if (retryDirty) {
      routingPatch.routing.retry = {
        on_status_codes: retryCodes.map(Number),
        on_timeout: retryTimeout,
      };
    }

    updateConfig.mutate(
      { gatewayId, config: routingPatch },
      {
        onSuccess: () => {
          enqueueSnackbar("Routing config updated", { variant: "success" });
          setDirty(false);
        },
        onError: () => {
          enqueueSnackbar("Failed to update routing config", {
            variant: "error",
          });
        },
      },
    );
  };

  return (
    <Stack spacing={2}>
      {/* Routing Strategy */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={1.5}>
            Routing Strategy
          </Typography>
          <TextField
            label="Default Strategy"
            select
            fullWidth
            value={strategy}
            onChange={(e) => handleStrategyChange(e.target.value)}
            size="small"
          >
            {ROUTING_STRATEGY_CHOICES.map((s) => (
              <MenuItem key={s.value} value={s.value}>
                {s.label}
              </MenuItem>
            ))}
          </TextField>
        </CardContent>
      </Card>

      {/* Failover */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={1.5}>
            Failover Configuration
          </Typography>
          <Stack spacing={2}>
            <Stack direction="row" alignItems="center" spacing={2}>
              <Typography variant="body2" sx={{ minWidth: 100 }}>
                Enabled
              </Typography>
              <Switch
                checked={failoverEnabled}
                onChange={(e) =>
                  handleChange(setFailoverEnabled)(e.target.checked)
                }
                size="small"
              />
            </Stack>
            <TextField
              label="Max Attempts"
              type="number"
              value={maxAttempts}
              onChange={(e) => handleChange(setMaxAttempts)(e.target.value)}
              size="small"
              disabled={!failoverEnabled}
              sx={{ maxWidth: 200 }}
            />
            <StatusCodeList
              testId="routing-failover-codes"
              label="Failover on Status Codes"
              helper="Failures with these codes move a request to another of your providers that serves the model, and down its model fallback chain. Empty: 429, 500, 502, 503 and 504."
              value={failoverCodes}
              onChange={handleChange(setFailoverCodes)}
              disabled={!failoverEnabled}
            />
          </Stack>
        </CardContent>
      </Card>

      {/* Retry */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={0.5}>
            Retry
          </Typography>
          <Typography variant="body2" color="text.secondary" mb={1.5}>
            Retries run when Retry is turned on (Fallbacks page:{" "}
            {retry?.enabled ? "on" : "off"}).
          </Typography>
          <Stack spacing={2}>
            <StatusCodeList
              testId="routing-retry-codes"
              label="Retry on Status Codes"
              helper="Failures with these codes retry the same provider before the request fails over. Empty: 429, 500, 502, 503 and 504."
              value={retryCodes}
              onChange={handleRetryChange(setRetryCodes)}
            />
            <Stack direction="row" alignItems="center" spacing={2}>
              <Switch
                checked={retryTimeout}
                onChange={(e) =>
                  handleRetryChange(setRetryTimeout)(e.target.checked)
                }
                size="small"
                inputProps={{
                  role: "switch",
                  "aria-label": "Retry on Timeout",
                }}
              />
              <Stack>
                <Typography variant="body2">Retry on Timeout</Typography>
                <Typography variant="caption" color="text.secondary">
                  Retries a provider call that timed out.
                </Typography>
              </Stack>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      {/* Rate Limiting */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={1.5}>
            Rate Limiting
          </Typography>
          <InfoLine label="Enabled" value={rateLimit?.enabled ? "Yes" : "No"} />
        </CardContent>
      </Card>

      {/* Save */}
      {dirty && (
        <Box display="flex" justifyContent="flex-end">
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              variant="contained"
              onClick={handleSave}
              disabled={updateConfig.isPending || !canEdit}
            >
              {updateConfig.isPending ? "Saving..." : "Save Routing Config"}
            </Button>
          </GatewayAdminOnly>
        </Box>
      )}

      {/* Advanced Routing (per-org config) */}
      <Card>
        <CardContent>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            mb={1.5}
          >
            <Typography variant="h6">Advanced Routing</Typography>
            <Button
              variant="contained"
              size="small"
              startIcon={<Iconify icon="mdi:cog" width={18} />}
              onClick={() => setEditorOpen(true)}
            >
              Configure Advanced Routing
            </Button>
          </Stack>
          <Typography variant="body2" color="text.secondary" mb={1.5}>
            Per-organization routing features: complexity-based routing,
            adaptive routing and provider locking.
          </Typography>
          <Stack spacing={1}>
            <InfoLine
              label="Complexity Routing"
              value={routing?.complexity?.enabled ? "Enabled" : "Disabled"}
            />
            <InfoLine
              label="Adaptive Routing"
              value={routing?.adaptive?.enabled ? "Enabled" : "Disabled"}
            />
            <InfoLine
              label="Provider Locking"
              value={
                routing?.provider_lock?.enabled ??
                routing?.providerLock?.enabled
                  ? "Enabled"
                  : "Disabled"
              }
            />
          </Stack>
        </CardContent>
      </Card>

      {/* Org Config Editor Dialog — opens to Routing tab */}
      <OrgConfigEditor
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onSave={handleEditorSave}
        initialConfig={activeConfig || null}
        isSaving={createMutation.isPending}
        defaultTab={2}
      />
    </Stack>
  );
};

const InfoLine = ({ label, value }) => (
  <Stack direction="row" spacing={2}>
    <Typography variant="body2" color="text.secondary" sx={{ minWidth: 160 }}>
      {label}
    </Typography>
    <Typography variant="body2">{String(value)}</Typography>
  </Stack>
);

InfoLine.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

RoutingConfigView.propTypes = {
  config: PropTypes.object,
  gatewayId: PropTypes.string,
};

export default RoutingConfigView;
