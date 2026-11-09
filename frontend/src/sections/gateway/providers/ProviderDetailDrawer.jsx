import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";

import { GatewayDetailDrawer, GatewayStatusChip } from "../components/frame";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { modelPath } from "../models/modelLinks";
import { orgModelPageId } from "../models/orgServed";
import { DetailFigure, DetailRow, DetailSection } from "./ui/DetailRows";
import FaIcon from "./ui/FaIcon";
import {
  formatCount,
  formatErrorRate,
  formatLatency,
  formatSeconds,
  PROVIDER_STATUS,
} from "./provider-rows";

// One provider: its status and last 24 hours from the org's own request
// logs, how the gateway reaches it, and the models it serves. The stored key
// is never sent back to the browser, so it is described, not shown. Edit and
// Remove are Owner or Admin only; for anyone else they show disabled and say
// why. Each model opens its model page (Overview, Try it, API) where
// `orgProviders`, the providers list the model page reads, has this provider
// serve it; any other model is shown without a link.
export default function ProviderDetailDrawer({
  row,
  onClose,
  canWrite,
  onEdit,
  onRemove,
  orgProviders,
}) {
  const open = Boolean(row);
  const status = PROVIDER_STATUS[row?.status] || PROVIDER_STATUS.unknown;
  // Analytics reads no provider parameter, so the link opens it as it is
  // (group by provider there) and claims no filter.
  const analyticsHref = paths.dashboard.gateway.analytics;

  return (
    <GatewayDetailDrawer
      open={open}
      onClose={onClose}
      title={row?.displayName || ""}
      subtitle={row ? `${row.name} · your own key` : undefined}
      actions={
        row ? (
          <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
            <GatewayAdminOnly allowed={Boolean(canWrite)}>
              <Button
                size="small"
                variant="outlined"
                startIcon={<FaIcon name="edit" />}
                onClick={() => onEdit(row)}
                disabled={!canWrite}
              >
                Edit
              </Button>
            </GatewayAdminOnly>
            <GatewayAdminOnly allowed={Boolean(canWrite)}>
              <Button
                size="small"
                variant="outlined"
                color="error"
                startIcon={<FaIcon name="delete" />}
                onClick={() => onRemove(row)}
                disabled={!canWrite}
              >
                Remove
              </Button>
            </GatewayAdminOnly>
          </Box>
        ) : null
      }
    >
      {row && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
          <DetailSection
            title="Last 24 hours"
            action={
              <Link
                component={RouterLink}
                href={analyticsHref}
                underline="hover"
                typography="s2"
                sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
              >
                Open Analytics
                <FaIcon name="open" size={14} />
              </Link>
            }
          >
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                gap: 2,
              }}
            >
              <DetailFigure
                label="Requests"
                value={formatCount(row.requests)}
              />
              <DetailFigure
                label="Avg latency"
                value={formatLatency(row.avgLatencyMs)}
              />
              <DetailFigure
                label="Error rate"
                value={formatErrorRate(row.errorRate)}
              />
            </Box>
            <DetailRow label="Status">
              <Box
                component="span"
                sx={{
                  display: "inline-flex",
                  flexDirection: "column",
                  gap: 0.5,
                }}
              >
                <GatewayStatusChip tone={status.tone} label={status.label} />
                {row.statusReason && (
                  <Typography typography="s2" color="text.secondary">
                    {row.statusReason}
                  </Typography>
                )}
              </Box>
            </DetailRow>
            <Typography typography="s2" color="text.secondary">
              From your own requests through the gateway.
            </Typography>
          </DetailSection>

          <Divider />

          <DetailSection title="Connection">
            <Box>
              <DetailRow label="Base URL" mono>
                {row.baseUrl || "—"}
              </DetailRow>
              <DetailRow label="API format">{row.apiFormat || "—"}</DetailRow>
              {row.apiFormat === "openai" && row.apiPathPrefix !== null && (
                <DetailRow label="Path prefix" mono>
                  {row.apiPathPrefix === "" ? "None" : row.apiPathPrefix}
                </DetailRow>
              )}
              <DetailRow label="API key">
                Stored encrypted. Never shown again.
              </DetailRow>
              <DetailRow label="Timeout">
                {formatSeconds(row.timeout)}
              </DetailRow>
              <DetailRow label="Max concurrent">
                {row.maxConcurrent ?? "—"}
              </DetailRow>
              <DetailRow label="Connection pool">
                {row.connPoolSize ?? "—"}
              </DetailRow>
            </Box>
          </DetailSection>

          <Divider />

          <DetailSection title={`Models (${row.models.length})`}>
            {row.models.length > 0 ? (
              <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                {row.models.map((model) => {
                  const pageId = orgModelPageId(orgProviders, row.id, model);
                  const href = pageId ? modelPath(pageId) : null;
                  return href ? (
                    <Chip
                      key={model}
                      label={model}
                      size="small"
                      variant="outlined"
                      clickable
                      component={RouterLink}
                      href={href}
                    />
                  ) : (
                    <Chip
                      key={model}
                      label={model}
                      size="small"
                      variant="outlined"
                    />
                  );
                })}
              </Box>
            ) : (
              <Typography typography="s1" color="text.secondary">
                No models selected. Edit the provider to choose some.
              </Typography>
            )}
          </DetailSection>
        </Box>
      )}
    </GatewayDetailDrawer>
  );
}

ProviderDetailDrawer.propTypes = {
  row: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  canWrite: PropTypes.bool,
  onEdit: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  // GET /agentcc/gateways/<id>/providers/ providers: which models link.
  orgProviders: PropTypes.array,
};
