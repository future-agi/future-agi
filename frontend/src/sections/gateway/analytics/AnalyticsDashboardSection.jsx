import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Tab, Typography } from "@mui/material";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";

import { paths } from "src/routes/paths";
import { CustomTabs } from "src/components/tabs/tabs";

import { useGatewayContext } from "../context/useGatewayContext";
import { GatewayEmptyState, GatewayPageFrame } from "../components/frame";
import AnalyticsKpis from "./AnalyticsKpis";
import UsageCharts from "./UsageCharts";
import RangeToggle from "./RangeToggle";
import CustomAnalyticsRangeDialog from "./CustomAnalyticsRangeDialog";
import CostAnalytics from "./CostAnalytics";
import LatencyAnalytics from "./LatencyAnalytics";
import ErrorAnalytics from "./ErrorAnalytics";
import ModelComparison from "./ModelComparison";
import { useAnalyticsOverview } from "./hooks/useAnalyticsOverview";
import { ANALYTICS_RANGES, rangeWindow } from "./analyticsRange";
import {
  analyticsReadLabel,
  parseAnalyticsQuery,
  refreshAnalyticsQuery,
  serializeAnalyticsQuery,
} from "./queryState";
import { zoneNote } from "../utils/timeZone";
import { useGatewayConfig } from "../providers/hooks/useGatewayConfig";
import { sectionEnforcement } from "../settings/sectionEnforcement";

const TABS = [
  { slug: "usage", label: "Usage" },
  { slug: "cost", label: "Cost" },
  { slug: "latency", label: "Latency" },
  { slug: "errors", label: "Errors" },
  { slug: "models", label: "Models" },
];
const PANELS = {
  usage: UsageCharts,
  cost: CostAnalytics,
  latency: LatencyAnalytics,
  errors: ErrorAnalytics,
  models: ModelComparison,
};

export default function AnalyticsDashboardSection({
  gatewayId: gatewayIdProp,
} = {}) {
  const { gatewayId: ctxGatewayId } = useGatewayContext();
  const gatewayId = gatewayIdProp || ctxGatewayId;
  const { tab } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const active = TABS.some((t) => t.slug === tab) ? tab : "usage";
  const search = searchParams.toString();
  // Only a new URL resolves an absent/invalid window. Canonicalizing once
  // preserves its clock sample across rerenders, tabs, reload and history.
  const state = useMemo(() => parseAnalyticsQuery(search), [search]);
  const canonical = serializeAnalyticsQuery(state);
  const { start, end, granularity } = state;
  const [customOpen, setCustomOpen] = useState(false);
  useEffect(() => {
    if (search !== canonical) setSearchParams(canonical, { replace: true });
  }, [search, canonical, setSearchParams]);

  const overview = useAnalyticsOverview({ start, end, gatewayId });
  const lastOverview = useRef({ gatewayId, at: 0 });
  useEffect(() => {
    if (lastOverview.current.gatewayId !== gatewayId)
      lastOverview.current = { gatewayId, at: 0 };
    if (
      !overview.error &&
      !overview.isPlaceholderData &&
      overview.dataUpdatedAt
    ) {
      lastOverview.current = { gatewayId, at: overview.dataUpdatedAt };
    }
  }, [
    gatewayId,
    overview.error,
    overview.isPlaceholderData,
    overview.dataUpdatedAt,
  ]);
  const panelQuery = (query) => {
    const [domain, name, params] = query.queryKey;
    return (
      domain === "analytics" &&
      name === active &&
      params?.start === start &&
      params?.end === end &&
      (params?.gateway_id || null) === (gatewayId || null)
    );
  };
  const panelFetching = useIsFetching({ predicate: panelQuery }) > 0;
  const cacheOff =
    sectionEnforcement(
      useGatewayConfig(gatewayId, { meta: { errorHandled: true } }),
      "cache",
    ) === "not_enforced";

  const refresh = () => {
    const next = refreshAnalyticsQuery(state);
    if (next.start !== start || next.end !== end) {
      // New query keys perform the reads. Never also refetch the old window.
      setSearchParams(serializeAnalyticsQuery(next));
    } else {
      overview.refetch();
      queryClient.refetchQueries({ type: "active", predicate: panelQuery });
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(
        `${window.location.origin}${location.pathname}?${canonical}`,
      );
      enqueueSnackbar("View link copied", { variant: "success" });
    } catch {
      enqueueSnackbar(
        "Could not copy the link. Copy the address from your browser.",
        { variant: "warning" },
      );
    }
  };
  const Panel = PANELS[active];
  const retainedOverview = overview.data != null && !overview.isPlaceholderData;

  return (
    <GatewayPageFrame
      title="Analytics"
      description={`Requests, cost, latency and errors through the gateway over time, by model and provider. ${zoneNote()}`}
      actions={
        <>
          <RangeToggle
            value={state.preset}
            options={ANALYTICS_RANGES}
            onChange={(preset) =>
              setSearchParams(
                serializeAnalyticsQuery({
                  ...state,
                  preset,
                  ...rangeWindow(preset),
                }),
              )
            }
          />
          <Button size="small" onClick={() => setCustomOpen(true)}>
            Custom interval
          </Button>
          <Button
            size="small"
            onClick={refresh}
            disabled={overview.isFetching || panelFetching}
          >
            Refresh
          </Button>
          <Button size="small" onClick={copy}>
            Copy view link
          </Button>
        </>
      }
    >
      {customOpen && (
        <CustomAnalyticsRangeDialog
          start={start}
          end={end}
          onClose={() => setCustomOpen(false)}
          onApply={(bounds) => {
            setSearchParams(
              serializeAnalyticsQuery({ ...state, ...bounds, preset: "" }),
            );
            setCustomOpen(false);
          }}
        />
      )}
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ overflowWrap: "anywhere" }}
      >
        {`${start} to ${end}${state.preset ? " · Rolling preset; Refresh advances this window." : " · Fixed snapshot; Refresh keeps these bounds."}`}
      </Typography>
      <Typography
        typography="s2"
        color="text.secondary"
        aria-label="Overview freshness"
      >
        {`Overview · ${analyticsReadLabel(overview, lastOverview.current.gatewayId === gatewayId ? lastOverview.current.at : 0)}. Panels load independently.`}
      </Typography>
      {(overview.isFetching || overview.isPlaceholderData) && (
        <Typography role="status" typography="s2">
          Updating overview…
        </Typography>
      )}
      {overview.error &&
        (retainedOverview ? (
          <Alert
            severity="warning"
            action={
              <Button
                size="small"
                color="inherit"
                onClick={() => overview.refetch()}
              >
                Retry
              </Button>
            }
          >
            Overview could not be refreshed. Showing stale data from the last
            successful load.
          </Alert>
        ) : (
          <GatewayEmptyState
            icon="alertCircle"
            title="Analytics could not be loaded"
            description={
              overview.error.message || "The gateway API did not answer."
            }
            actionLabel="Retry"
            onAction={() => overview.refetch()}
          />
        ))}
      {(!overview.error || retainedOverview) && (
        <AnalyticsKpis
          overview={overview.data}
          cacheOff={cacheOff}
          loading={overview.isLoading || overview.isPlaceholderData}
          period={
            ANALYTICS_RANGES.find((r) => r.value === state.preset)?.period ||
            "selected period"
          }
        />
      )}
      <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
        <CustomTabs
          value={active}
          onChange={(_event, slug) => {
            const root = paths.dashboard.gateway.analytics;
            navigate(
              `${slug === "usage" ? root : `${root}/${slug}`}?${canonical}`,
            );
          }}
          variant="scrollable"
          allowScrollButtonsMobile
          aria-label="Analytics views"
        >
          {TABS.map((t) => (
            <Tab key={t.slug} value={t.slug} label={t.label} />
          ))}
        </CustomTabs>
      </Box>
      {panelFetching && !["usage", "cost", "errors"].includes(active) && (
        <Typography role="status" typography="s2">
          Updating {TABS.find((t) => t.slug === active)?.label}…
        </Typography>
      )}
      <Box sx={{ minWidth: 0 }}>
        <Panel
          start={start}
          end={end}
          granularity={granularity}
          gatewayId={gatewayId}
          cacheOff={cacheOff}
          groupBy={state.groups[active]}
          onGroupByChange={(groupBy) =>
            setSearchParams(
              serializeAnalyticsQuery({
                ...state,
                groups: { ...state.groups, [active]: groupBy },
              }),
            )
          }
        />
      </Box>
    </GatewayPageFrame>
  );
}

AnalyticsDashboardSection.propTypes = { gatewayId: PropTypes.string };
