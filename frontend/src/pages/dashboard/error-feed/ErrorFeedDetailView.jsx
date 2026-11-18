import React, { useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  IconButton,
  Stack,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { useNavigate, useParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import { ShareDialog } from "src/components/share-dialog";
import { copyToClipboard } from "src/utils/utils";
import { useSnackbar } from "src/components/snackbar";
import {
  useErrorFeedDetail,
  useErrorFeedRedirect,
  useExportErrorFeedTraces,
  useUpdateErrorFeedIssue,
} from "src/api/errorFeed/error-feed";
import ErrorStatusChip from "./components/ErrorStatusChip";
import ErrorSeverityBadge from "./components/ErrorSeverityBadge";
import ErrorMetadataPanel, {
  LinearTeamPicker,
} from "./components/ErrorMetadataPanel";
import OverviewTab from "./components/OverviewTab";
import TracesTab from "./components/TracesTab";
import TrendsTab from "./components/TrendsTab";
import ClusterHeadlineCard from "./components/ClusterHeadlineCard";
import AnalyzeTab from "./components/AnalyzeTab";
import { useSharedIssue } from "./sharedIssueContext";
import { useErrorFeedStore } from "./store";
import { useAnalyzeRunner } from "./useAnalyzeRunner";
import { downloadCsv, tracesToCsv } from "./exportTracesCsv";

// ── Detail page skeleton ─────────────────────────────────────────────────────
function DetailSkeleton() {
  const pulse = {
    bgcolor: "action.selected",
    borderRadius: "4px",
    animation: "pulse 1.4s ease-in-out infinite",
    "@keyframes pulse": { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.4 } },
  };
  return (
    <Stack gap={2.5} p={2} flex={1}>
      <Stack gap={0.75}>
        <Box sx={{ ...pulse, height: 22, width: "55%" }} />
        <Box sx={{ ...pulse, height: 14, width: "30%" }} />
      </Stack>
      <Box sx={{ ...pulse, height: 100, width: "100%", borderRadius: 1 }} />
      <Box sx={{ ...pulse, height: 300, width: "100%", borderRadius: 1 }} />
    </Stack>
  );
}

// ── Tab label with optional badge ────────────────────────────────────────────
function TabLabel({ label, count, icon }) {
  return (
    <Stack direction="row" alignItems="center" gap={0.6}>
      {icon && <Iconify icon={icon} width={14} sx={{ opacity: 0.65 }} />}
      {label}
      {count != null && <Chip label={count} size="small" />}
    </Stack>
  );
}

TabLabel.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  icon: PropTypes.string,
};

// ── Tab definitions ──────────────────────────────────────────────────────────
const TABS = [
  { key: "overview", label: "Overview", icon: "solar:widget-4-linear" },
  { key: "traces", label: "Traces", icon: "solar:list-linear" },
  { key: "trends", label: "Trends", icon: "solar:graph-up-linear" },
  { key: "analyze", label: "Fix", icon: "ph:screwdriver" },
];

// ── Main view ─────────────────────────────────────────────────────────────────
export default function ErrorFeedDetailView() {
  // On the public /shared/<token> page the issue arrives with the share and
  // the page is read-only: no fetches, no triage, no analysis runs.
  const shared = useSharedIssue();
  const { id: routeId } = useParams();
  const id = shared ? shared.issue?.row?.cluster_id : routeId;
  const navigate = useNavigate();
  const { activeTab, setActiveTab } = useErrorFeedStore();
  const [linearPickerOpen, setLinearPickerOpen] = useState(false);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [clusterIdCopied, setClusterIdCopied] = useState(false);
  const setAnalyzePendingStart = useErrorFeedStore(
    (s) => s.setAnalyzePendingStart,
  );
  const {
    data: redirect,
    isLoading: redirectLoading,
    isError: redirectError,
  } = useErrorFeedRedirect(shared ? null : id);
  const resolvedId = shared ? id : redirect?.resolved_cluster_id;
  useEffect(() => {
    if (!shared && resolvedId && resolvedId !== id) {
      navigate(`/dashboard/error-feed/${resolvedId}`, { replace: true });
    }
  }, [id, navigate, resolvedId, shared]);
  const { data: fetchedDetail, isLoading } = useErrorFeedDetail(id, {
    enabled: !shared && resolvedId === id,
    retry: false,
  });
  const detail = shared ? shared.issue : fetchedDetail;
  const updateIssue = useUpdateErrorFeedIssue();
  const exportTraces = useExportErrorFeedTraces();
  const { enqueueSnackbar } = useSnackbar();

  const handleExportTraces = (clusterId) =>
    exportTraces.mutate(clusterId, {
      onSuccess: (overview) => {
        const traces = overview?.representative_traces ?? [];
        if (!traces.length) {
          enqueueSnackbar("No traces to export", { variant: "info" });
          return;
        }
        downloadCsv(tracesToCsv(traces), `error-feed-${clusterId}-traces.csv`);
        const total = overview?.representative_total ?? traces.length;
        if (total > traces.length) {
          enqueueSnackbar(
            `Exported the latest ${traces.length} of ${total} traces`,
            { variant: "warning" },
          );
        } else {
          enqueueSnackbar(
            `Exported ${traces.length} trace${traces.length === 1 ? "" : "s"}`,
            { variant: "success" },
          );
        }
      },
      onError: () =>
        enqueueSnackbar("Failed to export traces", { variant: "error" }),
    });

  const currentError = useMemo(() => {
    if (!detail?.row) return null;
    return {
      ...detail.row,
      description: detail.description,
      success_trace: detail.success_trace,
      representative_trace: detail.representative_trace,
      rca: detail.rca,
    };
  }, [detail]);

  // On a shared issue the runner only seeds the saved analysis for the Fix
  // tab; it never opens Falcon or starts a run.
  useAnalyzeRunner(currentError?.cluster_id, currentError, {
    enabled: !shared,
  });

  if (!shared && (redirectLoading || (resolvedId === id && isLoading))) {
    return <DetailSkeleton />;
  }
  if (!shared && (redirectError || !redirect)) {
    return <Box p={3}>This group could not be found.</Box>;
  }
  if (resolvedId !== id) {
    return <DetailSkeleton />;
  }
  if (!currentError) {
    return <Box p={3}>This group could not be loaded.</Box>;
  }

  const tabIndex = TABS.findIndex((t) => t.key === activeTab);
  const safeTabIndex = tabIndex === -1 ? 0 : tabIndex;
  // Gate tab content on the tab KEY, not the numeric index — reordering TABS
  // must never silently swap which panel renders. `safeTabIndex` is only used
  // to drive the MUI <Tabs> indicator.
  const activeTabKey = TABS[safeTabIndex].key;

  const handleCopyClusterId = async () => {
    // Same inline feedback as the product's other copy buttons: the icon
    // turns into a check for a moment; only a failure gets a toast.
    if (!(await copyToClipboard(currentError.cluster_id))) {
      enqueueSnackbar("Couldn't copy the cluster ID", { variant: "error" });
      return;
    }
    setClusterIdCopied(true);
    setTimeout(() => setClusterIdCopied(false), 1500);
  };

  return (
    <Box
      sx={{
        display: "flex",
        flex: 1,
        height: "100%",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* ── Main column ── */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minWidth: 0,
        }}
      >
        {/* ── Header ── */}
        <Box
          sx={{
            px: 2,
            pt: 1.5,
            pb: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
            bgcolor: "background.paper",
          }}
        >
          {/* Breadcrumb row */}
          <Stack direction="row" alignItems="center" gap={0.5} mb={1}>
            {shared ? (
              <Typography
                fontSize="12px"
                color="text.secondary"
                sx={{ px: 0.75 }}
              >
                Error Feed
              </Typography>
            ) : (
              <Button
                size="small"
                variant="text"
                startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
                onClick={() => navigate("/dashboard/error-feed")}
                sx={{
                  color: "text.secondary",
                  minWidth: 0,
                  "&:hover": { color: "text.primary", bgcolor: "action.hover" },
                }}
              >
                Error Feed
              </Button>
            )}
            <Iconify
              icon="solar:alt-arrow-right-linear"
              width={14}
              sx={{ color: "text.disabled" }}
            />
            <Chip
              label={currentError.error.type
                .replace(/Error$/, "")
                .replace(/([A-Z])/g, " $1")
                .trim()}
              size="small"
            />
          </Stack>

          {/* Error title */}
          <Stack
            direction="row"
            alignItems="flex-start"
            justifyContent="space-between"
            gap={2}
          >
            <Stack gap={0.5} flex={1} minWidth={0}>
              <Typography
                typography="m3"
                fontWeight="fontWeightSemiBold"
                color="text.primary"
                title={currentError.error.name}
                sx={{
                  lineHeight: 1.35,
                  overflow: "hidden",
                  display: "-webkit-box",
                  WebkitBoxOrient: "vertical",
                  WebkitLineClamp: 2,
                  overflowWrap: "anywhere",
                }}
              >
                {currentError.error.name}
              </Typography>
              <Stack
                direction="row"
                alignItems="center"
                gap={1.5}
                flexWrap="wrap"
              >
                <Stack direction="row" alignItems="center" gap={0.75}>
                  <Box
                    sx={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      bgcolor: "orange.500",
                      flexShrink: 0,
                    }}
                  />
                  <Typography typography="s2" color="text.disabled">
                    {currentError.error.type}
                  </Typography>
                </Stack>
                <ErrorStatusChip status={currentError.status} />
                <ErrorSeverityBadge
                  severity={currentError.severity}
                  assessmentStatus={currentError.severity_assessment_status}
                  source={currentError.severity_source}
                  reason={currentError.severity_reason}
                />
                {/* Cluster badge */}
                <Chip
                  icon={<Iconify icon="solar:layers-linear" width={14} />}
                  label={`${currentError.trace_count?.toLocaleString()} traces`}
                  size="small"
                />
              </Stack>
            </Stack>

            {/* Action buttons */}
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              flexShrink={0}
            >
              <Tooltip
                title={clusterIdCopied ? "Copied" : "Copy cluster ID"}
                arrow
              >
                <IconButton
                  size="small"
                  onClick={handleCopyClusterId}
                  sx={{ color: "text.secondary" }}
                >
                  <Iconify
                    icon={
                      clusterIdCopied
                        ? "solar:check-linear"
                        : "solar:copy-linear"
                    }
                    width={14}
                  />
                </IconButton>
              </Tooltip>
              {!shared && (
                <>
                  <Tooltip title="Share" arrow>
                    <IconButton
                      size="small"
                      onClick={() => setShareDialogOpen(true)}
                      sx={{ color: "text.secondary" }}
                    >
                      <Iconify icon="solar:share-linear" width={14} />
                    </IconButton>
                  </Tooltip>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={
                      <Iconify icon="solar:download-minimalistic-linear" width={14} />
                    }
                    disabled={
                      exportTraces.isPending || !currentError.trace_count
                    }
                    onClick={() => handleExportTraces(currentError.cluster_id)}
                    sx={{ borderColor: "divider", color: "text.secondary" }}
                  >
                    Export
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<Iconify icon="solar:check-linear" width={14} />}
                    disabled={updateIssue.isPending}
                    onClick={() =>
                      updateIssue.mutate({
                        clusterId: currentError.cluster_id,
                        status: "resolved",
                      })
                    }
                    sx={{ borderColor: "divider", color: "text.secondary" }}
                  >
                    Resolve
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={
                      <Iconify icon="solar:check-circle-linear" width={14} />
                    }
                    disabled={updateIssue.isPending}
                    onClick={() =>
                      updateIssue.mutate({
                        clusterId: currentError.cluster_id,
                        status: "acknowledged",
                      })
                    }
                    sx={{ borderColor: "divider", color: "text.secondary" }}
                  >
                    Acknowledge
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={
                      <Iconify icon="solar:eye-closed-linear" width={14} />
                    }
                    disabled={updateIssue.isPending}
                    onClick={() =>
                      updateIssue.mutate({
                        clusterId: currentError.cluster_id,
                        status: "escalating",
                      })
                    }
                    sx={{ borderColor: "divider", color: "text.secondary" }}
                  >
                    Ignore issue
                  </Button>
                </>
              )}
            </Stack>
          </Stack>
        </Box>

        {/* ── Tab bar ── */}
        <Box
          sx={{
            borderBottom: "1px solid",
            borderColor: "divider",
            px: 1.5,
            flexShrink: 0,
            bgcolor: "background.paper",
          }}
        >
          <Tabs sx={{ boxShadow: "none" }}
            value={safeTabIndex}
            onChange={(_, v) => setActiveTab(TABS[v].key)}
          >
            {TABS.map((tab) => (
              <Tab
                key={tab.key}
                label={
                  <TabLabel
                    label={tab.label}
                    icon={tab.icon}
                    count={
                      tab.key === "traces"
                        ? currentError?.trace_count
                        : undefined
                    }
                  />
                }
              />
            ))}
          </Tabs>
        </Box>

        {/* ── Tab content ── */}
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {activeTabKey === "analyze" ? (
            <Box
              sx={{
                flex: 1,
                minHeight: 0,
                p: 2,
                display: "flex",
                flexDirection: "column",
              }}
            >
              <AnalyzeTab error={currentError} />
            </Box>
          ) : (
            <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
              <Box sx={{ p: 2 }}>
                {activeTabKey === "overview" && (
                  <Box sx={{ mb: 2 }}>
                    <ClusterHeadlineCard
                      error={currentError}
                      onOpenAnalyze={() => setActiveTab("analyze")}
                      onStartAnalysis={() => {
                        setAnalyzePendingStart(currentError.cluster_id, true);
                        setActiveTab("analyze");
                      }}
                      onCreateLinear={() => setLinearPickerOpen(true)}
                    />
                  </Box>
                )}
                {activeTabKey === "overview" && (
                  <OverviewTab _error={currentError} />
                )}
                {activeTabKey === "traces" && (
                  <TracesTab error={currentError} />
                )}
                {activeTabKey === "trends" && (
                  <TrendsTab error={currentError} />
                )}
              </Box>
            </Box>
          )}
        </Box>
      </Box>

      {/* ── Right sidebar ── */}
      <ErrorMetadataPanel error={currentError} />

      {!shared && (
        <>
          <LinearTeamPicker
            open={linearPickerOpen}
            onClose={() => setLinearPickerOpen(false)}
            clusterId={currentError?.cluster_id}
            traceId={null}
          />
          <ShareDialog
            open={shareDialogOpen}
            onClose={() => setShareDialogOpen(false)}
            resourceType="error_feed_issue"
            resourceId={currentError.cluster_id}
          />
        </>
      )}
    </Box>
  );
}
