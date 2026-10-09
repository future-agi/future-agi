import React, { useState } from "react";
import {
  Stack,
  Card,
  CardContent,
  Typography,
  Button,
  Chip,
  Skeleton,
  Box,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { LocalDateTime } from "src/components/date-time/LocalDateTime";
import {
  useOrgConfig,
  useCreateOrgConfig,
} from "../providers/hooks/useOrgConfig";
import { useProviderHealth } from "../providers/hooks/useGatewayConfig";
import { useGatewayContext } from "../context/useGatewayContext";
import OrgConfigEditor from "./OrgConfigEditor";
import ConfigHistoryDrawer from "./ConfigHistoryDrawer";

const OrgConfigSection = () => {
  const { data: activeConfig, isLoading, error } = useOrgConfig();
  const { gatewayId } = useGatewayContext();
  const { data: providerHealth } = useProviderHealth(gatewayId);
  const createMutation = useCreateOrgConfig();
  const [editorOpen, setEditorOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  const handleSave = (configData) => {
    createMutation.mutate(configData, {
      onSuccess: () => setEditorOpen(false),
    });
  };

  if (isLoading) {
    return (
      <Stack spacing={2}>
        <Skeleton variant="rounded" width="100%" height={120} />
        <Stack direction="row" spacing={2}>
          <Skeleton variant="rounded" width="33%" height={80} />
          <Skeleton variant="rounded" width="33%" height={80} />
          <Skeleton variant="rounded" width="33%" height={80} />
        </Stack>
      </Stack>
    );
  }

  const hasConfig = activeConfig && !error;
  const guardrails = activeConfig?.guardrails || {};
  const routing = activeConfig?.routing || {};
  const cacheConfig = activeConfig?.cache || {};

  const providerCount = providerHealth?.providers?.length ?? 0;
  const checkCount =
    Object.keys(guardrails?.checks || {}).length ||
    guardrails?.rules?.length ||
    0;
  const cacheEnabled = cacheConfig.enabled || false;
  const cacheBackend = cacheConfig.backend || "memory";

  return (
    <Stack spacing={2}>
      {/* Active config card */}
      <Card>
        <CardContent>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="flex-start"
          >
            <Stack spacing={1}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Iconify
                  icon="mdi:tune-variant"
                  width={24}
                  sx={{ color: "primary.main" }}
                />
                <Typography variant="h6">Organization Config</Typography>
                {hasConfig && (
                  <Chip
                    label={`Version ${activeConfig.version || 1}`}
                    size="small"
                    color="primary"
                    variant="outlined"
                    sx={{ height: 22, fontSize: "0.7rem" }}
                  />
                )}
              </Stack>

              {hasConfig ? (
                <>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Box
                      sx={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        bgcolor: "success.main",
                      }}
                    />
                    <Typography variant="body2" fontWeight={500}>
                      Custom Config Active
                    </Typography>
                  </Stack>
                  {activeConfig.created_at && (
                    <Typography variant="caption" color="text.secondary">
                      Last Updated:{" "}
                      <LocalDateTime
                        value={activeConfig.created_at}
                        withTime
                        emptyText="No update time recorded"
                      />
                    </Typography>
                  )}
                  {activeConfig.change_description && (
                    <Typography variant="caption" color="text.secondary">
                      Change: &ldquo;{activeConfig.change_description}&rdquo;
                    </Typography>
                  )}
                </>
              ) : (
                <>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Box
                      sx={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        bgcolor: "text.disabled",
                      }}
                    />
                    <Typography variant="body2" fontWeight={500}>
                      Using Default Config
                    </Typography>
                  </Stack>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ maxWidth: 500 }}
                  >
                    Your organization is using the system defaults. Create a
                    custom config to set your own provider keys, guardrails, and
                    routing strategies.
                  </Typography>
                </>
              )}
            </Stack>

            <Stack direction="row" spacing={1}>
              {hasConfig && (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => setHistoryOpen(true)}
                >
                  View History
                </Button>
              )}
              <Button
                size="small"
                variant="contained"
                onClick={() => setEditorOpen(true)}
                startIcon={
                  <Iconify
                    icon={hasConfig ? "mdi:pencil" : "mdi:plus"}
                    width={18}
                  />
                }
              >
                {hasConfig ? "Edit Config" : "Create Custom Config"}
              </Button>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      {/* Summary cards */}
      {hasConfig && (
        <Stack direction="row" spacing={2}>
          <Card sx={{ flex: 1 }}>
            <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
              <Typography variant="caption" color="text.secondary">
                Providers
              </Typography>
              <Typography variant="h5">{providerCount}</Typography>
              <Typography variant="caption" color="text.secondary">
                {providerCount === 0
                  ? "No configured providers"
                  : `${providerCount} configured`}
              </Typography>
            </CardContent>
          </Card>
          <Card sx={{ flex: 1 }}>
            <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
              <Typography variant="caption" color="text.secondary">
                Guardrail Checks
              </Typography>
              <Typography variant="h5">{checkCount}</Typography>
              <Typography variant="caption" color="text.secondary">
                {checkCount === 0
                  ? "Using defaults"
                  : `${checkCount} overrides`}
              </Typography>
            </CardContent>
          </Card>
          <Card sx={{ flex: 1 }}>
            <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
              <Typography variant="caption" color="text.secondary">
                Routing Strategy
              </Typography>
              <Typography variant="h5" sx={{ textTransform: "capitalize" }}>
                {(routing.strategy || "default").replace(/_/g, " ")}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {routing.strategy ? "Custom" : "System default"}
              </Typography>
            </CardContent>
          </Card>
          <Card sx={{ flex: 1 }}>
            <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
              <Typography variant="caption" color="text.secondary">
                Cache
              </Typography>
              <Typography variant="h5" sx={{ textTransform: "capitalize" }}>
                {cacheEnabled ? cacheBackend.replace(/-/g, " ") : "Disabled"}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {cacheEnabled
                  ? `L1: ${cacheBackend}${cacheConfig.semantic?.enabled ? ` + L2: ${cacheConfig.semantic?.backend || "memory"}` : ""}`
                  : "Not configured"}
              </Typography>
            </CardContent>
          </Card>
        </Stack>
      )}

      <OrgConfigEditor
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onSave={handleSave}
        initialConfig={hasConfig ? activeConfig : null}
        isSaving={createMutation.isPending}
      />

      <ConfigHistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      />
    </Stack>
  );
};

export default OrgConfigSection;
