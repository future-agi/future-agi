import StatusBadge from "src/components/status-badge/StatusBadge";
import React from "react";
import PropTypes from "prop-types";
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
  Box,
} from "@mui/material";
import Section from "src/components/section";
import { useNavigate } from "react-router-dom";

const CHIP_ROLE = {
  success: "success",
  warning: "warning",
  error: "danger",
  default: "neutral",
};

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
      <Section variant="panel" sx={{ p: 4 }}>
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
      </Section>
    );
  }

  return (
    <Section variant="panel" sx={{ p: 0, overflow: "hidden" }}>
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
                    <StatusBadge
                      status={status.label}
                      label={status.label}
                      role={CHIP_ROLE[status.color] || "neutral"}
                    />
                  </TableCell>
                  <TableCell>
                    {Array.isArray(models) && models.length > 0 ? (
                      <Typography variant="body2" color="text.secondary">
                        {models.join(" · ")}
                      </Typography>
                    ) : modelCount != null ? (
                      <Typography variant="body2">{modelCount}</Typography>
                    ) : (
                      <Typography variant="body2" color="text.secondary">
                        {"\u2014"}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell sx={{ fontVariantNumeric: "tabular-nums" }}>
                    <Typography variant="body2">
                      {formatLatency(latency)}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ fontVariantNumeric: "tabular-nums" }}>
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
    </Section>
  );
};

ProviderHealthTable.propTypes = {
  providerHealth: PropTypes.object,
  orgConfig: PropTypes.object,
};

export default ProviderHealthTable;
