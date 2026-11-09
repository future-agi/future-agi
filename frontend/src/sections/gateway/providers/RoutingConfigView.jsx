import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Paper,
  Typography,
  Box,
  Stack,
  MenuItem,
  Autocomplete,
} from "@mui/material";
import {
  AdminButton as Button,
  AdminSwitch as Switch,
  AdminTextField as TextField,
  AdminChip as Chip,
} from "../components/GatewayWriteControls";
import { enqueueSnackbar } from "notistack";
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

// Status codes either list can match (contract K5: a list matches the
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
            variant="outlined"
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

// Providers > Routing: the org's routing strategy, provider failover and
// retry, as the gateway runs them for the org's keys (fallbackSwitches.js),
// on the gateway page's cards. Changing them is Owner or Admin only
// (useGatewayAdmin), as on Fallbacks and in the Org config editor: for
// anyone else every control, Configure Advanced Routing included, reads the
// stored value, is disabled and says why. The read-only lines stay as they
// are for everyone.
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
      <Section title="Routing Strategy">
        <GatewayAdminOnly allowed={canEdit} display="block">
          <TextField
            label="Default Strategy"
            select
            fullWidth
            value={strategy}
            onChange={(e) => handleStrategyChange(e.target.value)}
            size="small"
            disabled={!canEdit}
          >
            {ROUTING_STRATEGY_CHOICES.map((s) => (
              <MenuItem key={s.value} value={s.value}>
                {s.label}
              </MenuItem>
            ))}
          </TextField>
        </GatewayAdminOnly>
      </Section>

      {/* Failover */}
      <Section title="Failover Configuration">
        <Stack spacing={2}>
          <Stack direction="row" alignItems="center" spacing={2}>
            <Typography typography="s1" sx={{ minWidth: 100 }}>
              Enabled
            </Typography>
            <GatewayAdminOnly allowed={canEdit}>
              <Switch
                checked={failoverEnabled}
                onChange={(e) =>
                  handleChange(setFailoverEnabled)(e.target.checked)
                }
                size="small"
                disabled={!canEdit}
                inputProps={{ "aria-label": "Failover enabled" }}
              />
            </GatewayAdminOnly>
          </Stack>
          <GatewayAdminOnly allowed={canEdit} display="block">
            <TextField
              label="Max Attempts"
              type="number"
              value={maxAttempts}
              onChange={(e) => handleChange(setMaxAttempts)(e.target.value)}
              size="small"
              disabled={!canEdit || !failoverEnabled}
              sx={{ maxWidth: 200 }}
            />
          </GatewayAdminOnly>
          <GatewayAdminOnly allowed={canEdit} display="block">
            <StatusCodeList
              testId="routing-failover-codes"
              label="Failover on Status Codes"
              helper="Failures with these codes move a request to another of your providers that serves the model, and down its model fallback chain. Empty: 429, 500, 502, 503 and 504."
              value={failoverCodes}
              onChange={handleChange(setFailoverCodes)}
              disabled={!canEdit || !failoverEnabled}
            />
          </GatewayAdminOnly>
        </Stack>
      </Section>

      {/* Retry */}
      <Section title="Retry">
        <Typography typography="s1" color="text.secondary" mb={1.5}>
          Retries run when Retry is turned on (Fallbacks page:{" "}
          {retry?.enabled ? "on" : "off"}).
        </Typography>
        <Stack spacing={2}>
          <GatewayAdminOnly allowed={canEdit} display="block">
            <StatusCodeList
              testId="routing-retry-codes"
              label="Retry on Status Codes"
              helper="Failures with these codes retry the same provider before the request fails over. Empty: 429, 500, 502, 503 and 504."
              value={retryCodes}
              onChange={handleRetryChange(setRetryCodes)}
              disabled={!canEdit}
            />
          </GatewayAdminOnly>
          <Stack direction="row" alignItems="center" spacing={2}>
            <GatewayAdminOnly allowed={canEdit}>
              <Switch
                checked={retryTimeout}
                onChange={(e) =>
                  handleRetryChange(setRetryTimeout)(e.target.checked)
                }
                size="small"
                disabled={!canEdit}
                inputProps={{
                  role: "switch",
                  "aria-label": "Retry on Timeout",
                }}
              />
            </GatewayAdminOnly>
            <Stack>
              <Typography typography="s1">Retry on Timeout</Typography>
              <Typography typography="s2" color="text.secondary">
                Retries a provider call that timed out.
              </Typography>
            </Stack>
          </Stack>
        </Stack>
      </Section>

      {/* Rate Limiting */}
      <Section title="Rate Limiting">
        <InfoLine label="Enabled" value={rateLimit?.enabled ? "Yes" : "No"} />
      </Section>

      {/* Save */}
      {dirty && (
        <Box display="flex" justifyContent="flex-end">
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              variant="contained"
              color="primary"
              onClick={handleSave}
              disabled={updateConfig.isPending || !canEdit}
            >
              {updateConfig.isPending ? "Saving..." : "Save Routing Config"}
            </Button>
          </GatewayAdminOnly>
        </Box>
      )}

      {/* Advanced Routing (per-org config) */}
      <Section
        title="Advanced Routing"
        testId="routing-advanced"
        action={
          <GatewayAdminOnly allowed={canEdit}>
            <Button
              variant="outlined"
              size="small"
              onClick={() => setEditorOpen(true)}
              disabled={!canEdit}
            >
              Configure Advanced Routing
            </Button>
          </GatewayAdminOnly>
        }
      >
        <Typography typography="s1" color="text.secondary" mb={1.5}>
          Per-organization routing features: complexity-based routing, adaptive
          routing and provider locking. Scheduled completions, model access
          groups and traffic mirroring do not run on this gateway: a new or
          changed value is refused when saved.
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
              routing?.provider_lock?.enabled ?? routing?.providerLock?.enabled
                ? "Enabled"
                : "Disabled"
            }
          />
        </Stack>
      </Section>

      {/* Org Config Editor Dialog — opens to Routing tab */}
      <OrgConfigEditor
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onSave={handleEditorSave}
        initialConfig={activeConfig || null}
        isSaving={createMutation.isPending}
        defaultTab="routing"
      />
    </Stack>
  );
};

// A titled card on the gateway page surface.
const Section = ({ title, action, testId, children }) => (
  <Paper
    variant="outlined"
    component="section"
    data-testid={testId}
    sx={{ p: 2, borderRadius: 1 }}
  >
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      flexWrap="wrap"
      gap={1}
      mb={1.5}
    >
      <Typography component="h2" typography="m3" fontWeight="fontWeightMedium">
        {title}
      </Typography>
      {action}
    </Stack>
    {children}
  </Paper>
);

Section.propTypes = {
  title: PropTypes.string.isRequired,
  action: PropTypes.node,
  testId: PropTypes.string,
  children: PropTypes.node,
};

const InfoLine = ({ label, value }) => (
  <Stack direction="row" spacing={2} alignItems="center" data-info-line="">
    <Typography typography="s1" color="text.secondary" sx={{ minWidth: 160 }}>
      {label}
    </Typography>
    <Typography typography="s1">{String(value)}</Typography>
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
