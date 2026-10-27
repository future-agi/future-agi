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
import { useOrgConfig, useCreateOrgConfig } from "./hooks/useOrgConfig";
import OrgConfigEditor from "../../gateway/settings/OrgConfigEditor";
import {
  DEFAULT_ROUTING_STRATEGY,
  ROUTING_STRATEGY_OPTIONS,
} from "../constants/routing";

const RoutingConfigView = ({ config, gatewayId }) => {
  const routing = config?.routing || {};
  const failover = routing?.failover || {};
  const rateLimit = config?.rate_limiting ?? config?.rateLimiting ?? {};

  const [strategy, setStrategy] = useState(DEFAULT_ROUTING_STRATEGY);
  const [failoverEnabled, setFailoverEnabled] = useState(false);
  const [maxAttempts, setMaxAttempts] = useState("3");
  const [retryCodes, setRetryCodes] = useState([]);
  const [dirty, setDirty] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);

  const updateConfig = useUpdateConfig();
  const { data: activeConfig } = useOrgConfig();
  const createMutation = useCreateOrgConfig();

  const handleEditorSave = (configData) => {
    createMutation.mutate(configData, {
      onSuccess: () => setEditorOpen(false),
    });
  };

  useEffect(() => {
    setStrategy(routing?.strategy ?? DEFAULT_ROUTING_STRATEGY);
    setFailoverEnabled(failover?.enabled ?? false);
    setMaxAttempts(
      String(failover?.max_attempts ?? failover?.maxAttempts ?? 3),
    );
    setRetryCodes(
      (failover?.on_status_codes ?? failover?.onStatusCodes ?? []).map(String),
    );
    setDirty(false);
  }, [config]);

  const handleChange = (setter) => (value) => {
    setter(value);
    setDirty(true);
  };

  const handleSave = () => {
    const routingPatch = {
      routing: {
        strategy,
        failover: {
          enabled: failoverEnabled,
          max_attempts: Number(maxAttempts),
          on_status_codes: retryCodes.map(Number),
        },
      },
    };

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
            onChange={(e) => handleChange(setStrategy)(e.target.value)}
            size="small"
          >
            {ROUTING_STRATEGY_OPTIONS.map((s) => (
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
            <Autocomplete
              multiple
              freeSolo
              options={["429", "500", "502", "503", "504"]}
              value={retryCodes}
              onChange={(_, val) => handleChange(setRetryCodes)(val)}
              disabled={!failoverEnabled}
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
                  label="Retry on Status Codes"
                  placeholder="Type code + Enter"
                  size="small"
                />
              )}
            />
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
          <Button
            variant="contained"
            onClick={handleSave}
            disabled={updateConfig.isPending}
          >
            {updateConfig.isPending ? "Saving..." : "Save Routing Config"}
          </Button>
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
            Per-organization routing features: complexity-based routing, fastest
            response mode, adaptive routing, scheduled completions, provider
            locking, and model access groups.
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
              label="Scheduled Completions"
              value={routing?.scheduled?.enabled ? "Enabled" : "Disabled"}
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
            <InfoLine
              label="Access Groups"
              value={
                Object.keys(
                  routing?.access_groups ?? routing?.accessGroups ?? {},
                ).length > 0
                  ? `${Object.keys(routing?.access_groups ?? routing?.accessGroups ?? {}).length} groups`
                  : "None"
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
