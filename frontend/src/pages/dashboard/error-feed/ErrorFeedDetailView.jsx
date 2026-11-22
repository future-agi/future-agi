import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  alpha,
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Popover,
  Stack,
  Tab,
  Tabs,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import { useNavigate, useParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import { ShareDialog } from "src/components/share-dialog";
import { copyToClipboard } from "src/utils/utils";
import { useSnackbar } from "src/components/snackbar";
import {
  useErrorFeedDetail,
  useErrorFeedList,
  useErrorFeedRedirect,
  useExportErrorFeedTraces,
  useUpdateErrorFeedIssue,
} from "src/api/errorFeed/error-feed";
import IssueEpochAction from "./epoch/IssueEpochAction";
import IssueEpochStrip from "./epoch/IssueEpochStrip";
import RunEpochDrawer from "./epoch/RunEpochDrawer";
import ErrorMetadataPanel, {
  LinearTeamPicker,
} from "./components/ErrorMetadataPanel";
import OverviewTab from "./components/OverviewTab";
import TracesTab from "./components/TracesTab";
import TrendsTab from "./components/TrendsTab";
import AnalyzeTab from "./components/AnalyzeTab";
import { useSharedIssue } from "./sharedIssueContext";
import { useErrorFeedApiParams, useErrorFeedStore } from "./store";
import { useAnalyzeRunner } from "./useAnalyzeRunner";
import { downloadFile, tracesToCsv } from "./exportTracesCsv";
import { analysisToMarkdown, SAMPLE_TRACE_LIMIT } from "./issueAnalysis";

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
  { key: "analyze", label: "Root cause", icon: "solar:magnifer-linear" },
];

// ── Main view ─────────────────────────────────────────────────────────────────
export default function ErrorFeedDetailView() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  // On the public /shared/<token> page the issue arrives with the share and
  // the page is read-only: no fetches, no triage, no analysis runs.
  const shared = useSharedIssue();
  const { id: routeId } = useParams();
  const id = shared ? shared.issue?.row?.cluster_id : routeId;
  const navigate = useNavigate();
  const { activeTab, setActiveTab } = useErrorFeedStore();
  const baseApiParams = useErrorFeedApiParams();
  const navListParams = useMemo(
    () => ({ ...baseApiParams, limit: 200, offset: 0 }),
    [baseApiParams],
  );
  const issueListQuery = useErrorFeedList(navListParams, {
    enabled: !shared,
  });
  const issueIds = useMemo(
    () => (issueListQuery?.data?.data ?? []).map((r) => r.cluster_id),
    [issueListQuery?.data],
  );
  const currentIssueIdx = issueIds.indexOf(id);
  const prevIssueId = currentIssueIdx > 0 ? issueIds[currentIssueIdx - 1] : null;
  const nextIssueId =
    currentIssueIdx >= 0 && currentIssueIdx < issueIds.length - 1
      ? issueIds[currentIssueIdx + 1]
      : null;

  const [linearPickerOpen, setLinearPickerOpen] = useState(false);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [clusterIdCopied, setClusterIdCopied] = useState(false);
  const [exportAnchor, setExportAnchor] = useState(null);
  const [actionsAnchor, setActionsAnchor] = useState(null);
  const [shortcutsAnchor, setShortcutsAnchor] = useState(null);
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
  // Epoch on this issue: never on a shared page. Each part checks the org has Epoch
  // and reads the issue's epoch itself; the reads are shared.
  const [epochOpen, setEpochOpen] = useState(false);
  const updateIssue = useUpdateErrorFeedIssue();
  const exportTraces = useExportErrorFeedTraces();
  const { enqueueSnackbar } = useSnackbar();

  const handleExportTraces = (clusterId) =>
    exportTraces.mutate(
      { clusterId },
      {
        onSuccess: (overview) => {
          const traces = overview?.representative_traces ?? [];
          if (!traces.length) {
            enqueueSnackbar("No traces to export", { variant: "info" });
            return;
          }
          downloadFile(
            tracesToCsv(traces),
            `error-feed-${clusterId}-traces.csv`,
            "text/csv;charset=utf-8",
          );
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
      },
    );

  // Built from the stored analysis and an Overview sample; no model call.
  const handleExportAnalysis = (issue) =>
    exportTraces.mutate(
      { clusterId: issue.cluster_id, repLimit: SAMPLE_TRACE_LIMIT },
      {
        onSuccess: (overview) => {
          downloadFile(
            analysisToMarkdown(issue, overview),
            `error-feed-${issue.cluster_id}-analysis.md`,
            "text/markdown;charset=utf-8",
          );
          enqueueSnackbar("Exported the analysis", { variant: "success" });
        },
        onError: () =>
          enqueueSnackbar("Failed to export the analysis", {
            variant: "error",
          }),
      },
    );

  const currentError = useMemo(() => {
    if (!detail?.row) return null;
    return {
      ...detail.row,
      description: detail.description,
      fix_hypothesis: detail.fix_hypothesis,
      falsifier: detail.falsifier,
      success_trace: detail.success_trace,
      representative_trace: detail.representative_trace,
      rca: detail.rca,
    };
  }, [detail]);

  // On a shared issue the runner only seeds the saved analysis for the Root
  // cause tab; it never opens Falcon or starts a run.
  useAnalyzeRunner(currentError?.cluster_id, currentError, {
    enabled: !shared,
  });

  const goToPrevIssue = useCallback(() => {
    if (prevIssueId) navigate(`/dashboard/error-feed/${prevIssueId}`);
  }, [navigate, prevIssueId]);

  const goToNextIssue = useCallback(() => {
    if (nextIssueId) navigate(`/dashboard/error-feed/${nextIssueId}`);
  }, [navigate, nextIssueId]);

  // Global triage keyboard shortcuts (J/K next/prev issue, 1-4 tabs, E resolve, Esc list, ? help)
  useEffect(() => {
    if (shared) return undefined;
    const onKeyDown = (e) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (e.target?.tagName || "").toUpperCase();
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        e.target?.isContentEditable
      ) {
        return;
      }
      // Pause page shortcuts while any modal/drawer (including persistent TraceDetailDrawerV2) is open
      if (
        document.querySelector(
          '.MuiDrawer-modal .MuiDrawer-paper, [role="dialog"]',
        ) ||
        Array.from(
          document.querySelectorAll(".MuiDrawer-docked .MuiDrawer-paper"),
        ).some((el) => {
          const style = window.getComputedStyle(el);
          return (
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            el.getBoundingClientRect().width > 300
          );
        })
      ) {
        return;
      }
      if (e.key === "j" || e.key === "]") {
        if (nextIssueId) {
          e.preventDefault();
          navigate(`/dashboard/error-feed/${nextIssueId}`);
        }
      } else if (e.key === "k" || e.key === "[") {
        if (prevIssueId) {
          e.preventDefault();
          navigate(`/dashboard/error-feed/${prevIssueId}`);
        }
      } else if (e.key === "1") {
        setActiveTab("overview");
      } else if (e.key === "2") {
        setActiveTab("traces");
      } else if (e.key === "3") {
        setActiveTab("trends");
      } else if (e.key === "4") {
        setActiveTab("analyze");
      } else if (
        e.key === "e" &&
        currentError?.cluster_id &&
        !updateIssue.isPending
      ) {
        e.preventDefault();
        updateIssue.mutate({
          clusterId: currentError.cluster_id,
          status:
            currentError.status === "resolved" ? "for_review" : "resolved",
        });
      } else if (
        e.key === "Escape" &&
        !actionsAnchor &&
        !shortcutsAnchor &&
        !exportAnchor
      ) {
        navigate("/dashboard/error-feed");
      } else if (e.key === "?") {
        e.preventDefault();
        setShortcutsAnchor((cur) =>
          cur ? null : document.getElementById("ef-shortcuts-btn"),
        );
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    shared,
    nextIssueId,
    prevIssueId,
    navigate,
    setActiveTab,
    currentError,
    updateIssue,
    actionsAnchor,
    shortcutsAnchor,
    exportAnchor,
  ]);

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
          {/* Breadcrumb row + issue stepper & keyboard shortcuts */}
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            gap={1}
            mb={0.75}
          >
            <Stack
              direction="row"
              alignItems="center"
              gap={0.5}
              sx={{ minWidth: 0 }}
            >
              {shared ? (
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ px: 0.5 }}
                >
                  Error Feed
                </Typography>
              ) : (
                <Button
                  size="small"
                  variant="text"
                  startIcon={
                    <Iconify icon="solar:arrow-left-linear" width={14} />
                  }
                  onClick={() => navigate("/dashboard/error-feed")}
                  sx={{
                    color: "text.secondary",
                    minWidth: 0,
                    px: 0.5,
                    "&:hover": {
                      color: "text.primary",
                      bgcolor: "action.hover",
                    },
                  }}
                >
                  Error Feed
                </Button>
              )}
              {currentError.project && (
                <>
                  <Iconify
                    icon="solar:alt-arrow-right-linear"
                    width={13}
                    sx={{ color: "text.disabled", flexShrink: 0 }}
                  />
                  {!shared && currentError.project_id ? (
                    <Link
                      component="button"
                      type="button"
                      underline="hover"
                      onClick={() =>
                        navigate(`/dashboard/observe/${currentError.project_id}`)
                      }
                      sx={{
                        typography: "s3",
                        fontWeight: 500,
                        color: "text.primary",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 0.35,
                        cursor: "pointer",
                      }}
                    >
                      {currentError.project}
                      <Iconify
                        icon="solar:square-top-down-linear"
                        width={12}
                        sx={{ color: "text.secondary" }}
                      />
                    </Link>
                  ) : (
                    <Typography typography="s3" color="text.secondary" noWrap>
                      {currentError.project}
                    </Typography>
                  )}
                </>
              )}
              <Iconify
                icon="solar:alt-arrow-right-linear"
                width={13}
                sx={{ color: "text.disabled", flexShrink: 0 }}
              />
              <Typography typography="s3" color="text.secondary" noWrap>
                {currentError.error.type
                  .replace(/Error$/, "")
                  .replace(/([a-z])([A-Z])/g, "$1 $2")
                  .trim()}
              </Typography>
            </Stack>

            {!shared && (
              <Stack
                direction="row"
                alignItems="center"
                gap={0.5}
                sx={{ flexShrink: 0 }}
              >
                {currentIssueIdx >= 0 && (
                  <Stack
                    direction="row"
                    alignItems="center"
                    gap={0.25}
                    sx={{
                      bgcolor: "background.neutral",
                      borderRadius: "6px",
                      px: 0.4,
                      py: 0.15,
                    }}
                  >
                    <Tooltip title="Previous issue (K)" arrow>
                      <span>
                        <IconButton
                          size="small"
                          aria-label="Previous issue"
                          disabled={!prevIssueId}
                          onClick={goToPrevIssue}
                          sx={{ p: 0.25 }}
                        >
                          <Iconify
                            icon="solar:alt-arrow-left-linear"
                            width={14}
                          />
                        </IconButton>
                      </span>
                    </Tooltip>
                    <Typography
                      typography="s3"
                      color="text.secondary"
                      sx={{
                        px: 0.5,
                        fontVariantNumeric: "tabular-nums",
                        userSelect: "none",
                      }}
                    >
                      {currentIssueIdx + 1} / {issueIds.length}
                    </Typography>
                    <Tooltip title="Next issue (J)" arrow>
                      <span>
                        <IconButton
                          size="small"
                          aria-label="Next issue"
                          disabled={!nextIssueId}
                          onClick={goToNextIssue}
                          sx={{ p: 0.25 }}
                        >
                          <Iconify
                            icon="solar:alt-arrow-right-linear"
                            width={14}
                          />
                        </IconButton>
                      </span>
                    </Tooltip>
                  </Stack>
                )}
                <Tooltip title="Keyboard shortcuts (?)" arrow>
                  <IconButton
                    id="ef-shortcuts-btn"
                    size="small"
                    aria-label="Keyboard shortcuts"
                    onClick={(e) =>
                      setShortcutsAnchor((cur) => (cur ? null : e.currentTarget))
                    }
                    sx={{
                      bgcolor: "background.neutral",
                      p: 0.45,
                      color: "text.secondary",
                    }}
                  >
                    <Iconify icon="solar:keyboard-linear" width={15} />
                  </IconButton>
                </Tooltip>
              </Stack>
            )}
          </Stack>

          {/* Issue title + scannable Project / Users / Traces strip + actions */}
          <Stack
            direction="row"
            alignItems="flex-start"
            justifyContent="space-between"
            gap={2}
          >
            <Stack gap={0.6} flex={1} minWidth={0}>
              <Typography
                typography="m1"
                fontWeight={600}
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
                gap={0.85}
                flexWrap="wrap"
                sx={{ mt: 0.25 }}
              >
                {currentError.project && (
                  <Box
                    component={
                      !shared && currentError.project_id ? "button" : "div"
                    }
                    type={
                      !shared && currentError.project_id ? "button" : undefined
                    }
                    onClick={
                      !shared && currentError.project_id
                        ? () =>
                            navigate(
                              `/dashboard/observe/${currentError.project_id}`,
                            )
                        : undefined
                    }
                    sx={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 0.6,
                      px: 1.1,
                      py: 0.4,
                      borderRadius: "6px",
                      border: "none",
                      bgcolor: alpha(
                        theme.palette.primary.main,
                        isDark ? 0.14 : 0.08,
                      ),
                      color: "primary.main",
                      fontFamily: "inherit",
                      cursor:
                        !shared && currentError.project_id
                          ? "pointer"
                          : "default",
                      transition: "background-color 120ms",
                      ...(!shared && currentError.project_id
                        ? {
                            "&:hover": {
                              bgcolor: alpha(
                                theme.palette.primary.main,
                                isDark ? 0.22 : 0.14,
                              ),
                            },
                          }
                        : {}),
                    }}
                  >
                    <Iconify
                      icon="solar:folder-with-files-linear"
                      width={13}
                      sx={{ color: "primary.main", flexShrink: 0 }}
                    />
                    <Typography
                      typography="s2"
                      fontWeight={600}
                      color="inherit"
                      noWrap
                    >
                      {currentError.project}
                    </Typography>
                    {!shared && currentError.project_id && (
                      <Iconify icon="solar:arrow-right-up-linear" width={12} />
                    )}
                  </Box>
                )}

                <Stack
                  direction="row"
                  alignItems="center"
                  gap={0.6}
                  sx={{
                    px: 1.1,
                    py: 0.4,
                    borderRadius: "6px",
                    bgcolor: "background.neutral",
                  }}
                >
                  <Iconify
                    icon="solar:users-group-rounded-linear"
                    width={13}
                    sx={{ color: "text.secondary", flexShrink: 0 }}
                  />
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    <Box
                      component="strong"
                      sx={{ color: "text.primary", fontWeight: 600 }}
                    >
                      {currentError.users_affected?.toLocaleString() ?? 1}
                    </Box>{" "}
                    {currentError.users_affected === 1 ? "user" : "users"}{" "}
                    affected
                  </Typography>
                </Stack>

                <Box
                  component="button"
                  type="button"
                  onClick={() => setActiveTab("traces")}
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.6,
                    px: 1.1,
                    py: 0.4,
                    borderRadius: "6px",
                    border: "none",
                    bgcolor: "background.neutral",
                    color: "text.secondary",
                    fontFamily: "inherit",
                    cursor: "pointer",
                    transition: "background-color 120ms, color 120ms",
                    "&:hover": {
                      bgcolor: "action.selected",
                      color: "text.primary",
                    },
                  }}
                >
                  <Iconify
                    icon="solar:layers-linear"
                    width={13}
                    sx={{ color: "text.secondary", flexShrink: 0 }}
                  />
                  <Typography
                    typography="s2"
                    color="inherit"
                    sx={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    <Box
                      component="strong"
                      sx={{ color: "text.primary", fontWeight: 600 }}
                    >
                      {currentError.trace_count?.toLocaleString() ?? 0}
                    </Box>{" "}
                    linked {currentError.trace_count === 1 ? "trace" : "traces"}
                  </Typography>
                  <Iconify icon="solar:arrow-right-linear" width={12} />
                </Box>
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
                      <Iconify
                        icon="solar:download-minimalistic-linear"
                        width={14}
                      />
                    }
                    endIcon={
                      <Iconify icon="solar:alt-arrow-down-linear" width={14} />
                    }
                    disabled={exportTraces.isPending}
                    onClick={(e) => setExportAnchor(e.currentTarget)}
                    aria-haspopup="menu"
                    sx={{ borderColor: "divider", color: "text.secondary" }}
                  >
                    Export
                  </Button>
                  <Menu
                    anchorEl={exportAnchor}
                    open={Boolean(exportAnchor)}
                    onClose={() => setExportAnchor(null)}
                    anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
                    transformOrigin={{ vertical: "top", horizontal: "right" }}
                  >
                    <MenuItem
                      disabled={!currentError.trace_count}
                      onClick={() => {
                        setExportAnchor(null);
                        handleExportTraces(currentError.cluster_id);
                      }}
                    >
                      <ListItemIcon>
                        <Iconify icon="solar:document-text-linear" width={16} />
                      </ListItemIcon>
                      <ListItemText
                        primary="Traces"
                        secondary="CSV · ready to upload as a dataset"
                      />
                    </MenuItem>
                    <MenuItem
                      onClick={() => {
                        setExportAnchor(null);
                        handleExportAnalysis(currentError);
                      }}
                    >
                      <ListItemIcon>
                        <Iconify icon="solar:notes-linear" width={16} />
                      </ListItemIcon>
                      <ListItemText
                        primary="Analysis"
                        secondary="Markdown · cause, suggested change, pattern, traces"
                      />
                    </MenuItem>
                  </Menu>
                  <Button
                    size="small"
                    variant="contained"
                    disabled={updateIssue.isPending}
                    startIcon={
                      <Iconify
                        icon={
                          currentError.status === "resolved"
                            ? "solar:refresh-linear"
                            : "solar:check-circle-linear"
                        }
                        width={14}
                      />
                    }
                    onClick={() =>
                      updateIssue.mutate({
                        clusterId: currentError.cluster_id,
                        status:
                          currentError.status === "resolved"
                            ? "for_review"
                            : "resolved",
                      })
                    }
                  >
                    {currentError.status === "resolved" ? "Reopen" : "Resolve"}
                  </Button>
                  <IssueEpochAction
                    clusterId={currentError.cluster_id}
                    projectId={currentError.project_id}
                    onOpenDrawer={() => setEpochOpen(true)}
                  />
                  <IconButton
                    size="small"
                    aria-label="More issue actions"
                    onClick={(e) => setActionsAnchor(e.currentTarget)}
                    sx={{
                      bgcolor: "background.neutral",
                      color: "text.secondary",
                    }}
                  >
                    <Iconify icon="solar:menu-dots-bold" width={16} />
                  </IconButton>
                  <Menu
                    anchorEl={actionsAnchor}
                    open={Boolean(actionsAnchor)}
                    onClose={() => setActionsAnchor(null)}
                    PaperProps={{
                      sx: {
                        borderRadius: "8px",
                        minWidth: 185,
                        mt: 0.5,
                      },
                    }}
                  >
                    <MenuItem
                      disabled={currentError.status === "acknowledged"}
                      onClick={() => {
                        setActionsAnchor(null);
                        updateIssue.mutate({
                          clusterId: currentError.cluster_id,
                          status: "acknowledged",
                        });
                      }}
                    >
                      <ListItemIcon>
                        <Iconify icon="solar:check-circle-linear" width={16} />
                      </ListItemIcon>
                      <ListItemText primary="Acknowledge" />
                    </MenuItem>
                    <MenuItem
                      disabled={currentError.status === "escalating"}
                      onClick={() => {
                        setActionsAnchor(null);
                        updateIssue.mutate({
                          clusterId: currentError.cluster_id,
                          status: "escalating",
                        });
                      }}
                    >
                      <ListItemIcon>
                        <Iconify icon="solar:graph-up-linear" width={16} />
                      </ListItemIcon>
                      <ListItemText primary="Mark escalating" />
                    </MenuItem>
                    <Divider />
                    <MenuItem
                      onClick={() => {
                        setActionsAnchor(null);
                        setLinearPickerOpen(true);
                      }}
                    >
                      <ListItemIcon>
                        <Iconify icon="solar:add-square-linear" width={16} />
                      </ListItemIcon>
                      <ListItemText primary="Create Linear issue" />
                    </MenuItem>
                  </Menu>
                </>
              )}
            </Stack>
          </Stack>
        </Box>

        {!shared && (
          <IssueEpochStrip
            clusterId={currentError.cluster_id}
            projectId={currentError.project_id}
            onRunAgain={() => setEpochOpen(true)}
          />
        )}

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
          <Tabs
            sx={{ boxShadow: "none" }}
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
          <Popover
            open={Boolean(shortcutsAnchor)}
            anchorEl={shortcutsAnchor}
            onClose={() => setShortcutsAnchor(null)}
            anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
            transformOrigin={{ vertical: "top", horizontal: "right" }}
            PaperProps={{
              sx: {
                mt: 0.75,
                p: 1.5,
                width: 248,
                borderRadius: "8px",
                border: "1px solid",
                borderColor: "divider",
              },
            }}
          >
            <Typography
              typography="s2"
              fontWeight={600}
              color="text.primary"
              mb={1}
            >
              Keyboard shortcuts
            </Typography>
            <Stack gap={0.65}>
              {[
                ["J / ]", "Next issue"],
                ["K / [", "Previous issue"],
                ["1 – 4", "Switch tabs"],
                ["E", "Resolve / reopen"],
                ["Esc", "Back to Error Feed"],
                ["?", "Toggle this help"],
              ].map(([keyLabel, desc]) => (
                <Stack
                  key={keyLabel}
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                  gap={1}
                >
                  <Typography typography="s3" color="text.secondary">
                    {desc}
                  </Typography>
                  <Box
                    component="kbd"
                    sx={{
                      px: 0.75,
                      py: 0.15,
                      borderRadius: "4px",
                      bgcolor: "background.neutral",
                      border: "1px solid",
                      borderColor: "divider",
                      fontSize: "11px",
                      fontFamily: "monospace",
                      color: "text.primary",
                    }}
                  >
                    {keyLabel}
                  </Box>
                </Stack>
              ))}
            </Stack>
          </Popover>
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
          <RunEpochDrawer
            open={epochOpen}
            onClose={() => setEpochOpen(false)}
            clusterId={currentError.cluster_id}
            projectId={currentError.project_id}
            issueTitle={currentError.error?.name}
          />
        </>
      )}
    </Box>
  );
}
