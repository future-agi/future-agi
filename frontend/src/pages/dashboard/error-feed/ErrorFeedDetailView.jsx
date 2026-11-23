import React, { useEffect, useMemo } from "react";
import {
  Box,
  Chip,
  IconButton,
  Stack,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import Iconify from "src/components/iconify";
import { useErrorFeedDetail } from "src/api/errorFeed/mockAdapter";
import ErrorMetadataPanel from "./components/ErrorMetadataPanel";
import OverviewTab from "./components/OverviewTab";
import TracesTab from "./components/TracesTab";
import TrendsTab from "./components/TrendsTab";
import ClusterHeadlineCard from "./components/ClusterHeadlineCard";
import AnalyzeTab from "./components/AnalyzeTab";
import { useErrorFeedStore } from "./store";
import FixPage from "src/sections/simulate-v2/errorFeed/FixPage";
import useIssueRootCause from "./useIssueRootCause";
import ReopenedBanner from "src/sections/simulate-v2/errorFeed/ReopenedBanner";
import RegressionMemoryRow from "src/sections/simulate-v2/errorFeed/RegressionMemoryRow";
import FixButton from "src/sections/simulate-v2/errorFeed/FixButton";
import IssueProjectLine from "src/sections/simulate-v2/errorFeed/IssueProjectLine";
import { useIssueFix } from "src/sections/simulate-v2/errorFeed/issueFix";
import { useAnalyzeRunner } from "./useAnalyzeRunner";
import IssueActionsMenu from "./IssueActionsMenu";
import IssueFixStrip from "src/sections/simulate-v2/errorFeed/IssueFixStrip";
import ErrorStatusChip from "./components/ErrorStatusChip";
import ErrorSeverityBadge from "./components/ErrorSeverityBadge";
import {
  clickable,
  focusRing,
} from "src/sections/simulate-v2/components/clickable";

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
  /* The written analysis — "Fix" is the header action that proves a change. */
  { key: "analyze", label: "Root cause", icon: "solar:magnifier-linear" },
];

// ── Main view ─────────────────────────────────────────────────────────────────
/* "ToolArgumentMismatchError" → "Tool argument mismatch" — said once, in words. */
const errorTypeLabel = (type) => {
  const words = String(type || "")
    .replace(/Error$/, "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim()
    .toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "";
};

export default function ErrorFeedDetailView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { activeTab, setActiveTab } = useErrorFeedStore();
  const { data: detail, isLoading } = useErrorFeedDetail(id);
  const fixSheetOpen = useErrorFeedStore((s) => s.fixSheetOpen);
  const setFixSheetOpen = useErrorFeedStore((s) => s.setFixSheetOpen);
  /* Fix is a page of its own — this issue's address with ?fix=1, so Back
     returns to the issue and the link can be shared. Anything that asks for
     it through the store (the Root cause tab) lands here too. */
  const [params, setParams] = useSearchParams();
  const fixOpen = params.get("fix") === "1";
  /* `envId`: the environment picked in the Fix popup, kept on the plan. */
  const openFix = (envId) => {
    const next = new URLSearchParams(params);
    next.set("fix", "1");
    if (typeof envId === "string") next.set("env", envId);
    else next.delete("env");
    setParams(next);
  };
  const closeFix = () => {
    const next = new URLSearchParams(params);
    next.delete("fix");
    next.delete("env");
    setParams(next);
  };
  useEffect(() => {
    if (!fixSheetOpen) return;
    setFixSheetOpen(false);
    openFix();
  }, [fixSheetOpen]); // eslint-disable-line react-hooks/exhaustive-deps
  /* Back from building the agent's environment (Continue fixing): Fix opens
     where the person left it. The flag is read once, then cleared. */
  const location = useLocation();
  useEffect(() => {
    if (!location.state?.openFix) return;
    navigate(`${location.pathname}?fix=1`, { replace: true, state: null });
  }, [location.state?.openFix]); // eslint-disable-line react-hooks/exhaustive-deps

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

  useAnalyzeRunner(currentError?.cluster_id, currentError);
  /* Fix needs to know which agent the issue came from. */
  const { agent: fixAgent } = useIssueFix(currentError);
  /* Fix and Copy for coding agent carry the same root cause. */
  const rootCause = useIssueRootCause(currentError);
  /* Triage is the issue's own; the fix's status shows beside it, never in
     its place, so nothing here is locked by a fix. */

  if (isLoading || !currentError) {
    return <DetailSkeleton />;
  }
  if (fixOpen)
    return (
      <FixPage onClose={closeFix} issue={currentError} rootCause={rootCause} />
    );

  const tabIndex = TABS.findIndex((t) => t.key === activeTab);
  const safeTabIndex = tabIndex === -1 ? 0 : tabIndex;
  // Gate tab content on the tab KEY, not the numeric index — reordering TABS
  // must never silently swap which panel renders. `safeTabIndex` is only used
  // to drive the MUI <Tabs> indicator.
  const activeTabKey = TABS[safeTabIndex].key;

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
          {/* One object header (C3): Error Feed / title on one line, the
              issue's state and where it comes from on the next. Actions:
              one ⋯ menu and one primary — the fix action, an epoch on this
              issue. Status is changed in the right rail. */}
          <Stack direction="row" alignItems="center" gap={2}>
            <Stack gap={0.5} flex={1} minWidth={0}>
              <Stack direction="row" alignItems="center" gap={1} minWidth={0}>
                <Tooltip title="Back to Error Feed" arrow>
                  <IconButton
                    size="small"
                    aria-label="Back to Error Feed"
                    onClick={() => navigate("/dashboard/error-feed")}
                    sx={{ color: "text.secondary", ml: -0.5 }}
                  >
                    <Iconify icon="solar:arrow-left-linear" width={16} />
                  </IconButton>
                </Tooltip>
                <Stack
                  direction="row"
                  alignItems="baseline"
                  gap={0.75}
                  minWidth={0}
                >
                  <Typography
                    {...clickable(() => navigate("/dashboard/error-feed"), {
                      role: "link",
                    })}
                    sx={{
                      typography: "s1",
                      color: "text.secondary",
                      flexShrink: 0,
                      cursor: "pointer",
                      "&:hover": { color: "text.primary" },
                      ...focusRing,
                    }}
                  >
                    Error Feed
                  </Typography>
                  <Typography
                    sx={{ typography: "s1", color: "text.disabled", flexShrink: 0 }}
                  >
                    /
                  </Typography>
                  <Tooltip title={currentError.error.name} arrow>
                    <Typography
                      component="h1"
                      noWrap
                      sx={{
                        fontSize: 15,
                        lineHeight: "22px",
                        fontWeight: 600,
                        color: "text.primary",
                        minWidth: 0,
                      }}
                    >
                      {currentError.error.name}
                    </Typography>
                  </Tooltip>
                </Stack>
              </Stack>
              <Stack
                direction="row"
                alignItems="center"
                gap={1}
                minWidth={0}
                sx={{ pl: 4, overflow: "hidden" }}
              >
                {errorTypeLabel(currentError.error.type) && (
                  <Tooltip arrow title="Error type">
                    <Chip
                      size="small"
                      label={errorTypeLabel(currentError.error.type)}
                      sx={{ flexShrink: 0 }}
                    />
                  </Tooltip>
                )}
                <Box sx={{ flexShrink: 0 }}>
                  <ErrorStatusChip status={currentError.status} />
                </Box>
                <Box sx={{ flexShrink: 0 }}>
                  <ErrorSeverityBadge severity={currentError.severity} />
                </Box>
                {currentError.trace_count != null && (
                  <Chip
                    size="small"
                    icon={<Iconify icon="solar:layers-minimalistic-linear" width={12} />}
                    label={`${currentError.trace_count} ${currentError.trace_count === 1 ? "trace" : "traces"}`}
                    sx={{ flexShrink: 0 }}
                  />
                )}
                {/* The Tracing project it comes from, and the agent linked to it. */}
                <Box
                  sx={{
                    minWidth: 0,
                    flexShrink: 3,
                    overflow: "hidden",
                    whiteSpace: "nowrap",
                    "& .MuiTypography-root": {
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    },
                  }}
                >
                  <IssueProjectLine issue={currentError} />
                </Box>
              </Stack>
            </Stack>

            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              flexShrink={0}
            >
              {/* Copy link, Share, Export, hand to a coding agent. */}
              <IssueActionsMenu
                issue={currentError}
                agent={fixAgent}
                rootCause={rootCause}
              />
              {/* The one primary: Fix (an epoch on this issue), or what it
                  needs first — Connect agent, Link agent — or Retry Epoch N. */}
              <FixButton
                issue={currentError}
                onOpenSheet={openFix}
                rootCause={rootCause}
              />
            </Stack>
          </Stack>
        </Box>

        {/* What the fix action needs, or how the last epoch ended. */}
        <IssueFixStrip issue={currentError} />
        {/* A fix that didn't hold — linked to the change. */}
        <ReopenedBanner issue={currentError} />
        {/* Once the fix is released: keep its scenarios as must-pass? */}
        <RegressionMemoryRow issue={currentError} />

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
                  <Box sx={{ mb: 2 }}>
                    <ClusterHeadlineCard error={currentError} />
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
    </Box>
  );
}
