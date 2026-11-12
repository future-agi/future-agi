import React, { Suspense, lazy } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { isGatewayControlHidden } from "../shell/page-status";
import {
  CopyButton,
  GatewayHealthChip,
  KeyValueList,
  SettingsPanel,
} from "./ui";
import {
  NOT_REPORTED,
  enforcedSectionsText,
  formatMs,
  isBlock,
  lastHealthCheckText,
  notEnforcedSectionsText,
  onOff,
} from "./gatewayReport";
import { GATEWAY_MONO } from "../utils/fonts";
import { gatewayDisplayName } from "../utils/productName";

// While gateway credits are hidden (shell/page-status.js), the Billing &
// credits panel and the billing hooks behind it are not loaded at all.
const BillingCreditsPanel = isGatewayControlHidden("managedCredits")
  ? null
  : lazy(() => import("./BillingCreditsPanel"));

function HealthLine({ health }) {
  if (!health) return null;
  const when = health.at.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (health.ok) {
    const count = health.result?.provider_count;
    return (
      <Typography typography="s2" color="text.secondary">
        {`Gateway answered: ${health.result?.status || "healthy"}`}
        {Number.isFinite(count)
          ? ` · ${count} provider${count === 1 ? "" : "s"} configured`
          : ""}
        {` · checked at ${when}`}
      </Typography>
    );
  }
  return (
    <Typography typography="s2" color="error.main">
      {`${health.denied ? "Health check not authorized" : "Gateway did not answer"}: ${health.error || "health check failed"} · ${health.denied ? "attempted" : "checked"} at ${when}`}
    </Typography>
  );
}

HealthLine.propTypes = { health: PropTypes.object };

function NotReported({ what }) {
  return (
    <Typography typography="s1" color="text.secondary">
      {`This gateway did not report its ${what} settings.`}
    </Typography>
  );
}

NotReported.propTypes = { what: PropTypes.string.isRequired };

// General: who the gateway is, whether it answers and what it reports about
// itself, where its billing lives (central Settings > Billing), and email
// alerts. Every server, logging and enforcement value is the gateway's own
// (GET /-/config, passed through by the config read); a value it does not
// report reads "Not reported", never a default.
export default function GeneralTab({ gateway, config, health }) {
  const baseUrl = gateway?.baseUrl || "";
  const server = isBlock(config?.server) ? config.server : null;
  const logging = isBlock(config?.logging) ? config.logging : null;
  const costTracking = config?.cost_tracking ?? config?.costTracking ?? {};
  const notEnforced = notEnforcedSectionsText(config);

  return (
    <Stack spacing={2}>
      <SettingsPanel
        title="Gateway Info"
        description="The endpoint your applications call, whether it answers, and which organization settings it enforces."
        testId="gateway-identity"
      >
        <KeyValueList
          rows={[
            { label: "Name", value: gatewayDisplayName(gateway?.name) },
            {
              label: "Endpoint",
              value: baseUrl ? (
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 0.5,
                    minWidth: 0,
                  }}
                >
                  <Box
                    component="code"
                    sx={{
                      fontFamily: GATEWAY_MONO,
                      typography: "s1",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {baseUrl}
                  </Box>
                  <CopyButton text={baseUrl} label="Copy endpoint" />
                </Box>
              ) : (
                "Gateway URL not configured"
              ),
            },
            {
              label: "Status",
              value: (
                <Stack spacing={0.5} alignItems="flex-start">
                  <GatewayHealthChip status={gateway?.status} />
                  <HealthLine health={health} />
                </Stack>
              ),
            },
            {
              label: "Providers",
              value: String(gateway?.providerCount ?? "—"),
            },
            { label: "Models", value: String(gateway?.modelCount ?? "—") },
            {
              label: "Last Health Check",
              value: lastHealthCheckText(config, gateway),
            },
            {
              label: "Org Sections Enforced",
              value: enforcedSectionsText(config),
            },
            {
              label: "Org Sections Not Enforced",
              value: notEnforced,
              hidden: !notEnforced,
            },
          ]}
        />
      </SettingsPanel>

      <SettingsPanel
        title="Server Configuration"
        description="The timeouts this gateway node runs with."
        testId="gateway-server"
      >
        {server ? (
          <KeyValueList
            rows={[
              {
                label: "Host",
                value:
                  typeof server.host === "string"
                    ? server.host || "All interfaces"
                    : NOT_REPORTED,
              },
              { label: "Port", value: String(server.port ?? NOT_REPORTED) },
              {
                label: "Read Timeout",
                value: formatMs(server.read_timeout_ms),
              },
              {
                label: "Write Timeout",
                value: formatMs(server.write_timeout_ms),
              },
              {
                label: "Idle Timeout",
                value: formatMs(server.idle_timeout_ms),
              },
              {
                label: "Max Request Time",
                value: formatMs(server.max_request_timeout_ms),
              },
            ]}
          />
        ) : (
          <NotReported what="server" />
        )}
      </SettingsPanel>

      <SettingsPanel
        title="Logging"
        description="What this gateway node writes to its request log."
        testId="gateway-logging"
      >
        {logging ? (
          <KeyValueList
            rows={[
              { label: "Level", value: logging.level || NOT_REPORTED },
              {
                label: "Request Logging",
                value: onOff(logging.request_logging, "Enabled", "Disabled"),
              },
              {
                label: "Include Bodies",
                value: onOff(logging.include_bodies, "Yes", "No"),
              },
              {
                label: "Content Mode",
                value: logging.content_mode || NOT_REPORTED,
              },
            ]}
          />
        ) : (
          <NotReported what="logging" />
        )}
      </SettingsPanel>

      <SettingsPanel
        title="Cost Tracking"
        description="Whether this gateway prices the requests it serves."
        testId="gateway-cost-tracking"
      >
        <KeyValueList
          rows={[
            { label: "Enabled", value: costTracking.enabled ? "Yes" : "No" },
          ]}
        />
      </SettingsPanel>

      {BillingCreditsPanel && (
        <Suspense fallback={null}>
          <BillingCreditsPanel />
        </Suspense>
      )}

      {/* Alerts are set on the Alerts page (rules and channels), the one
          alerting surface; General has no second, email-only one. */}
    </Stack>
  );
}

GeneralTab.propTypes = {
  gateway: PropTypes.object,
  // The gateway config read (useGatewayConfig): its server, logging and
  // org_sections_* blocks are the gateway's own.
  config: PropTypes.object,
  health: PropTypes.shape({
    ok: PropTypes.bool,
    denied: PropTypes.bool,
    at: PropTypes.instanceOf(Date),
    result: PropTypes.object,
    error: PropTypes.string,
  }),
};
