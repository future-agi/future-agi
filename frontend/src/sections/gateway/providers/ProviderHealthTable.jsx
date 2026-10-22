import React from "react";
import PropTypes from "prop-types";
import {
  Card,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
  Chip,
  Box,
  Stack,
} from "@mui/material";
import { useNavigate } from "react-router-dom";

const STATUS_MAP = {
  healthy: { label: "Healthy", color: "success" },
  degraded: { label: "Degraded", color: "warning" },
  unhealthy: { label: "Unhealthy", color: "error" },
  not_pushed: { label: "Not pushed", color: "error" },
  unknown: { label: "Unknown", color: "default" },
};

// The status the providers action derives comes first: a
// degraded provider is still healthy (status != unhealthy), so `healthy`
// alone cannot tell it apart. A provider the config push leaves out
// (pushed: false) is never shown with a health chip.
function getProviderStatus(provider) {
  if (!provider) return STATUS_MAP.unknown;
  if (provider.pushed === false) return STATUS_MAP.not_pushed;
  if (provider.status) return STATUS_MAP[provider.status] || STATUS_MAP.unknown;
  if (provider.healthy === true) return STATUS_MAP.healthy;
  if (provider.healthy === false) return STATUS_MAP.unhealthy;
  return STATUS_MAP.unknown;
}

// How the status is derived (views/gateway.py providers).
const THRESHOLDS_TEXT =
  "Status comes from your organization's own requests to each provider in the last 24 hours: " +
  "Unhealthy at 50% failed or more, Degraded at 10% or more, counted from 5 requests; " +
  "fewer than 5 requests read as Healthy. Guardrail blocks and requests served by managed " +
  "models are not counted. The gateway's circuit breakers belong to the operator's " +
  "providers and are not shown here.";

function formatLatency(ms) {
  if (ms == null) return "\u2014";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
}

const ProviderHealthTable = ({ providerHealth, orgConfig }) => {
  const navigate = useNavigate();
  // providerHealth can be { providers: [...] } or { providers: { name: {...} } }
  const providers = providerHealth?.providers;
  let providerList = [];
  // Models from org config to enrich health data
  const orgProviders = orgConfig?.providers || {};

  if (Array.isArray(providers)) {
    providerList = providers.map((p) => ({
      ...p,
      originalName: p.name || p.provider_name || p.id,
    }));
  } else if (providers && typeof providers === "object") {
    providerList = Object.entries(providers).map(([name, info]) => ({
      name,
      originalName: name,
      ...(typeof info === "object" ? info : {}),
    }));
  }

  if (providerList.length === 0) {
    return (
      <Card sx={{ p: 4 }}>
        <Box
          display="flex"
          justifyContent="center"
          alignItems="center"
          minHeight={200}
        >
          <Typography color="text.secondary">
            No provider data available.
          </Typography>
        </Box>
      </Card>
    );
  }

  return (
    <Card>
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ px: 2, pt: 2, pb: 1 }}
      >
        {THRESHOLDS_TEXT}
      </Typography>
      <TableContainer>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Provider</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Models</TableCell>
              <TableCell>Avg Latency (24 h)</TableCell>
              <TableCell>Error Rate (24 h)</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {providerList.map((p, idx) => {
              const status = getProviderStatus(p);
              const originalName =
                p.originalName || p.name || p.id || `Provider ${idx + 1}`;
              const displayName =
                (p.display_name || "").trim() ||
                (originalName
                  ? originalName.charAt(0).toUpperCase() + originalName.slice(1)
                  : originalName);
              const models =
                p.models || orgProviders[originalName]?.models || [];
              const modelCount = Array.isArray(models)
                ? models.length
                : p.modelCount ?? p.model_count ?? null;
              // The org's average latency over the last 24 h; 0 means no
              // requests in the window.
              const rawLatency = p.avg_latency ?? null;
              const latency = rawLatency === 0 ? null : rawLatency;

              // Error rate: compute from errorCount/requestCount or use errorRate/success_rate
              const reqCount = p.request_count ?? 0;
              let errorRate = p.error_rate ?? null;
              if (errorRate == null && reqCount > 0 && p.success_rate != null) {
                errorRate = (1 - Number(p.success_rate)) * 100;
              } else if (errorRate == null && reqCount > 0) {
                const errCount = p.errorCount ?? p.error_count ?? 0;
                errorRate = (errCount / reqCount) * 100;
              }

              const statusReason = p.status_reason || "";

              return (
                <TableRow
                  key={displayName}
                  hover
                  sx={{ cursor: "pointer" }}
                  onClick={() =>
                    navigate(
                      `/dashboard/gateway/analytics?provider=${encodeURIComponent(displayName)}`,
                    )
                  }
                >
                  <TableCell>
                    <Typography variant="body2" fontWeight={600}>
                      {displayName}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Stack spacing={0.5} alignItems="flex-start">
                      <Chip
                        label={status.label}
                        color={status.color}
                        size="small"
                        variant="outlined"
                      />
                      {statusReason && (
                        <Typography variant="caption" color="text.secondary">
                          {statusReason}
                        </Typography>
                      )}
                    </Stack>
                  </TableCell>
                  <TableCell>
                    {Array.isArray(models) && models.length > 0 ? (
                      <Stack direction="row" flexWrap="wrap" gap={0.5}>
                        {models.map((m) => (
                          <Chip
                            key={m}
                            label={m}
                            size="small"
                            variant="outlined"
                          />
                        ))}
                      </Stack>
                    ) : modelCount != null ? (
                      <Typography variant="body2">{modelCount}</Typography>
                    ) : (
                      <Typography variant="body2" color="text.secondary">
                        {"\u2014"}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">
                      {formatLatency(latency)}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">
                      {errorRate != null
                        ? `${Number(errorRate).toFixed(1)}%`
                        : "\u2014"}
                    </Typography>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </Card>
  );
};

ProviderHealthTable.propTypes = {
  providerHealth: PropTypes.object,
  orgConfig: PropTypes.object,
};

export default ProviderHealthTable;
