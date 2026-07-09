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
import { formatProviderName } from "./utils/formatProviderName";

const STATUS_MAP = {
  healthy: { label: "Healthy", color: "success" },
  degraded: { label: "Degraded", color: "warning" },
  unhealthy: { label: "Unhealthy", color: "error" },
  unknown: { label: "Unknown", color: "default" },
};

function getProviderStatus(provider) {
  if (!provider) return STATUS_MAP.unknown;
  if (provider.status) return STATUS_MAP[provider.status] || STATUS_MAP.unknown;
  if (provider.healthy === true) return STATUS_MAP.healthy;
  if (provider.healthy === false) return STATUS_MAP.unhealthy;
  return STATUS_MAP.unknown;
}

function formatLatency(ms) {
  if (ms == null) return "\u2014";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
}

const ProviderHealthTable = ({ providerHealth, orgConfig, providerCredentials }) => {
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
      <TableContainer>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Provider</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Models</TableCell>
              <TableCell>Latency (P50)</TableCell>
              <TableCell>Error Rate</TableCell>
              <TableCell>Circuit Breaker</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {providerList.map((p, idx) => {
              const status = getProviderStatus(p);
              const displayName = formatProviderName(p, idx);
              const originalName =
                p.originalName || p.name || p.provider_name || p.id || "";
              const models =
                p.models || orgProviders[originalName]?.models || [];
              const modelCount = Array.isArray(models)
                ? models.length
                : p.modelCount ?? p.model_count ?? null;
              const rawLatency =
                p.latencyP50 ??
                p.latency_p50 ??
                p.latencyEwmaMs ??
                p.latency_ewma_ms ??
                p.avg_latency ??
                null;
              // Treat 0 as no data (EWMA starts at 0 when no requests tracked)
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

              const circuitState =
                p.circuitState ??
                p.circuit_state ??
                p.circuitBreakerState ??
                p.circuit_breaker_state ??
                null;

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
                    <Chip
                      label={status.label}
                      color={status.color}
                      size="small"
                      variant="outlined"
                    />
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
                  <TableCell>
                    <Typography variant="body2" color="text.secondary">
                      {circuitState ?? "\u2014"}
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
  providerHealth: PropTypes.shape({
    providers: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
  }),
  orgConfig: PropTypes.shape({
    providers: PropTypes.object,
  }),
  providerCredentials: PropTypes.arrayOf(
    PropTypes.shape({
      provider_name: PropTypes.string,
      display_name: PropTypes.string,
    }),
  ),
};

export default ProviderHealthTable;
