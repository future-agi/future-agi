import React, { useCallback, useMemo, useState } from "react";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import PageHeader from "src/components/page-header";
import {
  useErrorFeedList,
  useErrorFeedStats,
  useObserveProjectList,
} from "src/api/errorFeed/error-feed";
import { useErrorFeedAlerts } from "src/api/errorFeed/alerts";
import { useIntegrationConnections } from "src/api/integrations";
import ErrorFeedFilters from "./components/ErrorFeedFilters";
import ErrorFeedTable from "./components/ErrorFeedTable";
import { deriveFeedPageState } from "./feedPageState";
import { useErrorFeedApiParams, useErrorFeedStore } from "./store";

// Re-read on every visit/focus: a first Observe project is created by SDK
// instrumentation, not by a mutation this page could invalidate, so a user
// coming back within the catalog's 5-minute freshness window must still see
// it. Bounded to this page; no polling.
const REVALIDATE_ON_ENTRY = {
  refetchOnMount: "always",
  refetchOnWindowFocus: "always",
};

export default function ErrorFeedView() {
  const navigate = useNavigate();
  const [selected, setSelected] = useState([]);
  const [alertBannerDismissed, setAlertBannerDismissed] = useState(false);

  const apiParams = useErrorFeedApiParams();
  const selectedProject = useErrorFeedStore((s) => s.selectedProject);
  const feed = useErrorFeedList(apiParams, REVALIDATE_ON_ENTRY);
  const stats = useErrorFeedStats(
    selectedProject ? { project_id: selectedProject } : {},
    REVALIDATE_ON_ENTRY,
  );
  const catalog = useObserveProjectList(REVALIDATE_ON_ENTRY);
  const alertsQuery = useErrorFeedAlerts();
  const connectionsQuery = useIntegrationConnections();

  const alertRules = alertsQuery.data ?? [];
  const activeAlertCount = alertRules.filter((r) => r.enabled !== false).length;
  const slackConnected = (connectionsQuery.data ?? []).some(
    (c) => c.platform === "slack",
  );
  const escalatingCount = stats.data?.escalating ?? 0;

  const pageState = deriveFeedPageState({ catalog, feed });
  const rows = useMemo(() => feed.data?.data ?? [], [feed.data]);
  const totalCount = feed.data?.total ?? 0;

  const projectOptions = useMemo(
    () => [{ value: "", label: "All Projects" }, ...(catalog.data ?? [])],
    [catalog.data],
  );

  const handleRetry = useCallback(() => {
    // Revalidate the composed page, including malformed 200 responses that
    // render as errors even though React Query marks the request successful.
    feed.refetch();
    catalog.refetch();
  }, [feed, catalog]);

  const handleSelect = (clusterId, checked) => {
    setSelected((prev) =>
      checked ? [...prev, clusterId] : prev.filter((id) => id !== clusterId),
    );
  };

  const handleSelectAll = (checked, ids) => {
    setSelected(checked ? ids : []);
  };

  const handleClearSelection = () => setSelected([]);

  const openAlertsSetup = () => {
    const q = new URLSearchParams();
    if (activeAlertCount === 0) q.set("create", "1");
    if (selectedProject && !String(selectedProject).includes(",")) {
      q.set("project_id", selectedProject);
    }
    const qs = q.toString();
    navigate(`/dashboard/error-feed/alerts${qs ? `?${qs}` : ""}`);
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        height: "100%",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* ── Page Header ── */}
      <Box
        sx={{
          px: 2,
          pt: 2,
          pb: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <PageHeader
          sx={{ mb: 0 }}
          title="Error Feed"
          badge={
            <Typography
              sx={{
                typography: "s1",
                fontWeight: 400,
                color: "text.tertiary",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {totalCount}
            </Typography>
          }
          description="Agent failures grouped by root cause."
          actions={
            <>
              <Button
                variant={activeAlertCount === 0 ? "contained" : "outlined"}
                size="small"
                startIcon={<Iconify icon="solar:bell-bing-linear" width={15} />}
                onClick={openAlertsSetup}
                sx={
                  activeAlertCount > 0
                    ? { borderColor: "divider", color: "text.primary" }
                    : undefined
                }
              >
                {activeAlertCount > 0 ? (
                  <Stack direction="row" alignItems="center" gap={0.75}>
                    <span>Alerts</span>
                    <Box
                      component="span"
                      sx={{
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        bgcolor: "status.success.solid",
                      }}
                    />
                    <span>{activeAlertCount} active</span>
                  </Stack>
                ) : (
                  "Set up alerts"
                )}
              </Button>
              <Button
                variant="outlined"
                size="small"
                startIcon={
                  <SvgColor
                    src="/assets/icons/ic_docs_single.svg"
                    sx={{ width: 15 }}
                  />
                }
                component="a"
                href="https://docs.futureagi.com/docs/error-feed"
                target="_blank"
                sx={{
                  borderColor: "divider",
                  color: "text.secondary",
                  "&:hover": { borderColor: "border.hover" },
                }}
              >
                Docs
              </Button>
            </>
          }
        />
      </Box>

      {/* ── Content ── */}
      <Box
        sx={{
          flex: 1,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          gap: 1.25,
          p: 2,
        }}
      >
        {/* Unmissable but calm Slack & email alert setup strip when no alerts are active */}
        {!alertsQuery.isLoading &&
          activeAlertCount === 0 &&
          !alertBannerDismissed && (
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              gap={1.5}
              sx={{
                px: 1.5,
                py: 0.9,
                borderRadius: "6px",
                border: "1px solid",
                borderColor: "status.warning.border",
                bgcolor: "status.warning.bg",
                flexShrink: 0,
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                gap={1}
                sx={{ minWidth: 0 }}
              >
                <Iconify
                  icon="solar:bell-bing-linear"
                  width={16}
                  sx={{ color: "status.warning.fg", flexShrink: 0 }}
                />
                <Typography typography="s2" color="text.primary" noWrap>
                  <strong>
                    {slackConnected
                      ? "No Slack or email alerts active"
                      : "Slack & email alerts are not configured"}
                  </strong>
                  {" — "}
                  {escalatingCount > 0
                    ? `${escalatingCount.toLocaleString()} escalating ${escalatingCount === 1 ? "issue isn't" : "issues aren't"} notifying your team.`
                    : "set up Slack or email notifications when issues escalate."}
                </Typography>
              </Stack>
              <Stack
                direction="row"
                alignItems="center"
                gap={0.75}
                sx={{ flexShrink: 0 }}
              >
                <Button
                  size="small"
                  variant="outlined"
                  onClick={openAlertsSetup}
                  endIcon={
                    <Iconify icon="solar:arrow-right-linear" width={13} />
                  }
                  sx={{
                    borderColor: "status.warning.border",
                    color: "text.primary",
                    bgcolor: "background.paper",
                    whiteSpace: "nowrap",
                  }}
                >
                  Set up Slack / email alert
                </Button>
                <IconButton
                  size="small"
                  aria-label="Dismiss alert setup notice"
                  onClick={() => setAlertBannerDismissed(true)}
                  sx={{ color: "text.secondary" }}
                >
                  <Iconify icon="solar:close-linear" width={14} />
                </IconButton>
              </Stack>
            </Stack>
          )}

        {/* Unified single-row Filter + Queue SegmentedTabs toolbar */}
        <ErrorFeedFilters
          selected={selected}
          onClearSelection={handleClearSelection}
          projectOptions={projectOptions}
          stats={stats.data}
        />

        {/* Table */}
        <ErrorFeedTable
          rows={rows}
          totalCount={totalCount}
          isLoading={feed.isLoading}
          pageState={pageState}
          onRetry={handleRetry}
          selected={selected}
          onSelect={handleSelect}
          onSelectAll={handleSelectAll}
        />
      </Box>
    </Box>
  );
}
