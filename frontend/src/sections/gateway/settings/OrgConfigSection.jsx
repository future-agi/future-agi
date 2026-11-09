import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { LocalDateTime } from "src/components/date-time/LocalDateTime";
import { getErrorMessage } from "../../settings/integrations/utils";

import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayStatusChip,
} from "../components/frame";
import { useGatewayContext } from "../context/useGatewayContext";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import {
  useGatewayConfig,
  useProviderHealth,
} from "../providers/hooks/useGatewayConfig";
import {
  useCreateOrgConfig,
  useOrgConfig,
} from "../providers/hooks/useOrgConfig";
import ConfigHistoryDrawer from "./ConfigHistoryDrawer";
import OrgConfigEditor from "./OrgConfigEditor";
import { orgConfigSections } from "./orgConfigSections";
import { Figure, SettingsPanel } from "./ui";
import { EMPTY } from "../utils/formatters";
import { guardrailsAreOff } from "../utils/guardrailPipeline";
import { sectionEnforcement } from "./sectionEnforcement";

// An org cache TTL as the gateway reads it: seconds (Django stores 300 for
// "5m") or the duration text an older save kept; 300 s when none is set.
function formatCacheTtl(value) {
  if (value === undefined || value === null || value === "") return "5m";
  if (typeof value === "string" && !/^\d+$/.test(value.trim())) return value;
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return String(value);
  if (seconds > 0 && seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds > 0 && seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

// The Cache figure shows only what the gateway applies for an org: on or off
// and the TTL. Whether this gateway runs response caching at all comes from
// its own org_sections_enforced, never from anything else.
function cacheSummary(cacheConfig, gatewayConfigQuery) {
  const state = sectionEnforcement(gatewayConfigQuery, "cache");
  if (state === "not_enforced") {
    return {
      value: "Off on this gateway",
      caption: cacheConfig.enabled
        ? "Saved, not applied: response caching is off on this gateway"
        : "Response caching is off on this gateway.",
    };
  }
  if (!cacheConfig.enabled) {
    return { value: "Disabled", caption: "Not configured" };
  }
  const ttl = `TTL ${formatCacheTtl(cacheConfig.default_ttl ?? cacheConfig.defaultTtl)}`;
  const unknown = state === "unknown";
  return {
    value: "Enabled",
    caption: unknown ? `${ttl} · enforcement unknown` : ttl,
  };
}

// Centred in the 40 px row, the dash as well as the chips.
function GapCell({ value }) {
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: 1 }}>
      {value ? (
        <GatewayStatusChip tone={value.tone} label={value.label} />
      ) : (
        <Typography typography="s2" color="text.disabled">
          —
        </Typography>
      )}
    </Box>
  );
}

GapCell.propTypes = { value: PropTypes.object };

// The reasons, wrapped to at most two lines (the full text is the cell's
// tooltip), so no row is cut off at one line.
function WhyCell({ value }) {
  const text = (value || []).join(" ");
  return (
    <Box sx={{ display: "flex", alignItems: "center", minHeight: 40, py: 1 }}>
      <Typography
        typography="s2"
        color={text ? "text.primary" : "text.disabled"}
        sx={{
          display: "-webkit-box",
          WebkitBoxOrient: "vertical",
          WebkitLineClamp: 2,
          overflow: "hidden",
          whiteSpace: "normal",
          lineHeight: 1.5,
        }}
      >
        {text || EMPTY}
      </Typography>
    </Box>
  );
}

WhyCell.propTypes = { value: PropTypes.arrayOf(PropTypes.string) };

const COLUMNS = [
  { field: "section", headerName: "Section", minWidth: 170, flex: 1 },
  { field: "saved", headerName: "Saved", minWidth: 160, flex: 1 },
  {
    field: "gap",
    headerName: "On this gateway",
    minWidth: 230,
    flex: 1.2,
    cellRenderer: GapCell,
    comparator: (a, b) => (a?.label || "").localeCompare(b?.label || ""),
  },
  {
    field: "reasons",
    headerName: "Why",
    minWidth: 260,
    flex: 2.5,
    wrapText: true,
    autoHeight: true,
    cellRenderer: WhyCell,
    valueFormatter: ({ value }) => (value || []).join(" "),
    tooltipValueGetter: ({ value }) => (value || []).join("\n\n") || null,
  },
];

// Gateway settings > Org config: the organization's active gateway config,
// a few headline figures, and one row per section with what it holds and
// where this gateway does not apply it. A row opens the editor at its tab.
// Anyone may open the editor and the version history to read them; saving a
// config and activating a version are Owner or Admin only (the editor and the
// history drawer gate their own write controls).
export default function OrgConfigSection() {
  const { canEdit } = useGatewayAdmin();
  const { data: activeConfig, isLoading, error, refetch } = useOrgConfig();
  const { gatewayId } = useGatewayContext();
  const { data: providerHealth } = useProviderHealth(gatewayId);
  const gatewayConfigQuery = useGatewayConfig(gatewayId);
  const createMutation = useCreateOrgConfig();
  const [editorTab, setEditorTab] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const navigate = useNavigate();

  const hasConfig = Boolean(activeConfig);
  const rows = useMemo(
    () =>
      orgConfigSections(hasConfig ? activeConfig : null, gatewayConfigQuery),
    [activeConfig, hasConfig, gatewayConfigQuery],
  );

  if (isLoading) {
    return (
      <Stack spacing={2} data-testid="org-config-loading">
        <Skeleton variant="rounded" height={140} />
        <Skeleton variant="rounded" height={320} />
      </Stack>
    );
  }

  if (error) {
    return (
      <GatewayEmptyState
        icon="alertCircle"
        title="Organization config could not be loaded"
        description={
          getGatewayAuthenticationErrorMessage(error) ||
          getErrorMessage(
            error,
            "Retry to check which configuration is active.",
          )
        }
        actionLabel="Retry"
        onAction={() => refetch()}
      />
    );
  }

  const guardrails = activeConfig?.guardrails || {};
  const routing = activeConfig?.routing || {};
  const cacheConfig = activeConfig?.cache || {};
  const providerCount = providerHealth?.providers?.length ?? 0;
  const checkCount =
    Object.keys(guardrails?.checks || {}).length ||
    guardrails?.rules?.length ||
    0;
  const cache = cacheSummary(cacheConfig, gatewayConfigQuery);

  return (
    <Stack spacing={2}>
      <SettingsPanel
        title="Organization config"
        description={
          canEdit
            ? "Your organization's gateway settings. Every save becomes a new version and is pushed to the gateway."
            : "Your organization's gateway settings. Only an organization Owner or Admin can change it."
        }
        actions={
          <>
            {hasConfig && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => setHistoryOpen(true)}
              >
                View History
              </Button>
            )}
            {/* A member or viewer opens the config to read it (View Config);
                creating one is a write, Owner or Admin only. */}
            <GatewayAdminOnly allowed={canEdit || hasConfig}>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => setEditorTab("providers")}
                disabled={!canEdit && !hasConfig}
              >
                {!hasConfig
                  ? "Create Custom Config"
                  : canEdit
                    ? "Edit Config"
                    : "View Config"}
              </Button>
            </GatewayAdminOnly>
          </>
        }
      >
        {hasConfig ? (
          <Stack spacing={0.75}>
            <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
              <GatewayStatusChip tone="healthy" label="Custom config active" />
              <Typography typography="s2" color="text.secondary">
                {`Version ${activeConfig.version || 1}`}
              </Typography>
            </Stack>
            {activeConfig.created_at && (
              <Typography typography="s2" color="text.secondary">
                Last updated:{" "}
                <LocalDateTime
                  value={activeConfig.created_at}
                  withTime
                  emptyText="No update time recorded"
                />
              </Typography>
            )}
            {activeConfig.change_description && (
              <Typography typography="s2" color="text.secondary">
                Change: &ldquo;{activeConfig.change_description}&rdquo;
              </Typography>
            )}
          </Stack>
        ) : (
          <Stack spacing={0.75}>
            <Box>
              <GatewayStatusChip
                tone="neutral"
                label="Using the default config"
              />
            </Box>
            <Typography typography="s2" color="text.secondary">
              {canEdit
                ? "Your organization uses the gateway's defaults. Create a config to set your own guardrails, routing, cache and limits."
                : "Your organization uses the gateway's defaults."}
            </Typography>
          </Stack>
        )}

        {hasConfig && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr 1fr",
                md: "repeat(4, minmax(0, 1fr))",
              },
              gap: 2,
            }}
          >
            <Figure
              label="Providers"
              value={String(providerCount)}
              caption={
                providerCount === 0
                  ? "No configured providers"
                  : `${providerCount} configured`
              }
            />
            <Figure
              label="Guardrail checks"
              value={String(checkCount)}
              caption={
                guardrailsAreOff(gatewayConfigQuery)
                  ? "Not enforced: guardrails are off on this gateway"
                  : checkCount === 0
                    ? "Using defaults"
                    : `${checkCount} overrides`
              }
            />
            <Figure
              label="Routing strategy"
              value={(routing.strategy || "default").replace(/[_-]/g, " ")}
              caption={routing.strategy ? "Custom" : "System default"}
            />
            <Figure
              label="Cache"
              value={cache.value}
              caption={cache.caption}
              testId="org-config-cache"
            />
          </Box>
        )}
      </SettingsPanel>

      <SettingsPanel
        title="Sections"
        description="What each section holds, and where this gateway does not apply it. Select a section to open it in the editor; MCP opens its own page."
      >
        {rows.length ? (
          <GatewayGrid
            rowData={rows}
            columnDefs={COLUMNS}
            stateKey="gateway-org-config-sections"
            getRowId={(params) => params.data.id}
            // Rows grow with their Why text, so the grid sizes to its rows.
            height="auto"
            domLayout="autoHeight"
            tooltipShowDelay={300}
            onRowClicked={(event) =>
              event.data.opens
                ? navigate(event.data.opens)
                : setEditorTab(event.data.tab)
            }
            rowStyle={{ cursor: "pointer" }}
          />
        ) : (
          <GatewayEmptyState title="No sections" />
        )}
      </SettingsPanel>

      <OrgConfigEditor
        open={editorTab !== null}
        onClose={() => setEditorTab(null)}
        onSave={(configData) =>
          createMutation.mutate(configData, {
            onSuccess: () => setEditorTab(null),
          })
        }
        initialConfig={hasConfig ? activeConfig : null}
        isSaving={createMutation.isPending}
        defaultTab={editorTab ?? "providers"}
      />

      <ConfigHistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      />
    </Stack>
  );
}
