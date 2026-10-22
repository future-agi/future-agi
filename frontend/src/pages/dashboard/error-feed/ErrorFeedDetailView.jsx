import React, { useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  Tooltip,
  Typography,
  useTheme,
  alpha,
} from "@mui/material";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import {
  useErrorFeedDetail,
  useUpdateErrorFeedIssue,
} from "src/api/errorFeed/mockAdapter";
import ErrorSeverityBadge from "./components/ErrorSeverityBadge";
import ErrorMetadataPanel, {
  LinearTeamPicker,
} from "./components/ErrorMetadataPanel";
import OverviewTab from "./components/OverviewTab";
import TracesTab from "./components/TracesTab";
import TrendsTab from "./components/TrendsTab";
import ClusterHeadlineCard from "./components/ClusterHeadlineCard";
import AnalyzeTab from "./components/AnalyzeTab";
import { useErrorFeedStore } from "./store";
import FixPage from "src/sections/simulate-v2/errorFeed/FixPage";
import IssueStatusChip from "./components/IssueStatusChip";
import ErrorStatusChip from "./components/ErrorStatusChip";
import useIssueRootCause from "./useIssueRootCause";
import ReopenedBanner from "src/sections/simulate-v2/errorFeed/ReopenedBanner";
import RegressionMemoryRow from "src/sections/simulate-v2/errorFeed/RegressionMemoryRow";
import FixButton from "src/sections/simulate-v2/errorFeed/FixButton";
import IssueProjectLine from "src/sections/simulate-v2/errorFeed/IssueProjectLine";
import { useIssueFix } from "src/sections/simulate-v2/errorFeed/issueFix";
import { triageFixEffect } from "src/sections/simulate-v2/errorFeed/issueFixModel";
import { useAnalyzeRunner } from "./useAnalyzeRunner";
import IssueBriefButton from "./IssueBriefButton";

// ── Detail page skeleton ─────────────────────────────────────────────────────
function DetailSkeleton() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const pulse = {
    bgcolor: isDark ? alpha("#fff", 0.07) : alpha("#000", 0.07),
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
      {count != null && (
        <Chip
          label={count}
          size="small"
          sx={{
            height: 16,
            fontSize: "10px",
            fontWeight: 600,
            borderRadius: "3px",
            bgcolor: "action.hover",
            color: "text.disabled",
            "& .MuiChip-label": { px: "5px" },
          }}
        />
      )}
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
  { key: "overview", label: "Overview", icon: "mdi:view-dashboard-outline" },
  { key: "traces", label: "Traces", icon: "mdi:timeline-text-outline" },
  { key: "trends", label: "Trends", icon: "mdi:chart-line" },
  /* The written analysis — "Fix" is the header action that proves a change. */
  { key: "analyze", label: "Root cause", icon: "mdi:magnify-scan" },
];

// ── Main view ─────────────────────────────────────────────────────────────────
/* "ToolArgumentMismatchError" → "Tool argument mismatch" — said once, in words. */
const errorTypeLabel = (type) => {
  const words = String(type || "").replace(/Error$/, "").replace(/([a-z])([A-Z])/g, "$1 $2").trim().toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "";
};
/* "2h ago" — the same short form the list uses. */
const agoLabel = (iso) => {
  const m = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  if (!Number.isFinite(m)) return "";
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
};

export default function ErrorFeedDetailView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { activeTab, setActiveTab } = useErrorFeedStore();
  const [linearPickerOpen, setLinearPickerOpen] = useState(false);
  const setAnalyzePendingStart = useErrorFeedStore(
    (s) => s.setAnalyzePendingStart,
  );
  const { data: detail, isLoading } = useErrorFeedDetail(id);
  const updateIssue = useUpdateErrorFeedIssue();
  const fixSheetOpen = useErrorFeedStore((s) => s.fixSheetOpen);
  const setFixSheetOpen = useErrorFeedStore((s) => s.setFixSheetOpen);
  const [triageAnchor, setTriageAnchor] = useState(null);
  const copyText = (text, msg) => {
    navigator.clipboard?.writeText(text).then(() => enqueueSnackbar(msg, { variant: "success" })).catch(() => {});
  };
  /* Fix is a page of its own — this issue's address with ?fix=1, so Back
     returns to the issue and the link can be shared. Anything that asks for
     it through the store (the Root cause tab) lands here too. */
  const [params, setParams] = useSearchParams();
  const fixOpen = params.get("fix") === "1";
  const openFix = () => { const next = new URLSearchParams(params); next.set("fix", "1"); setParams(next); };
  const closeFix = () => { const next = new URLSearchParams(params); next.delete("fix"); setParams(next); };
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
  const { agent: fixAgent, status: fixStatus, setReopened } = useIssueFix(currentError);
  /* Fix and Copy for coding agent carry the same root cause. */
  const rootCause = useIssueRootCause(currentError);
  /* Triage is the issue's own; the fix's status shows beside it, never in
     its place, so nothing here is locked by a fix. */

  if (isLoading || !currentError) {
    return <DetailSkeleton />;
  }
  if (fixOpen) return <FixPage onClose={closeFix} issue={currentError} rootCause={rootCause} />;

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
          {/* Breadcrumb · the issue's ID, and the two utilities */}
          <Stack direction="row" alignItems="center" gap={0.5} mb={0.75}>
            <Button
              size="small"
              variant="text"
              startIcon={<Iconify icon="mdi:arrow-left" width={14} />}
              onClick={() => navigate("/dashboard/error-feed")}
              sx={{
                height: 26,
                fontSize: "12px",
                color: "text.secondary",
                px: 0.75,
                minWidth: 0,
                "&:hover": { color: "text.primary", bgcolor: "action.hover" },
              }}
            >
              Error Feed
            </Button>
            <Iconify icon="mdi:chevron-right" width={13} sx={{ color: "text.disabled" }} />
            <Typography
              sx={{ fontSize: "12px", color: "text.secondary", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
            >
              {currentError.cluster_id}
            </Typography>
            <Box flex={1} />
            <Tooltip title="Copy issue ID" arrow>
              <IconButton size="small" onClick={() => copyText(currentError.cluster_id, "Issue ID copied")} sx={{ color: "text.secondary" }}>
                <Iconify icon="mdi:content-copy" width={14} />
              </IconButton>
            </Tooltip>
            <Tooltip title="Copy link" arrow>
              <IconButton size="small" onClick={() => copyText(window.location.href, "Link copied")} sx={{ color: "text.secondary" }}>
                <Iconify icon="mdi:link-variant" width={15} />
              </IconButton>
            </Tooltip>
          </Stack>

          {/* Title, and the three actions: triage, hand off, fix */}
          <Stack direction="row" alignItems="flex-start" justifyContent="space-between" gap={2}>
            <Stack gap={0.75} flex={1} minWidth={0}>
              <Typography
                typography="m3"
                fontWeight="fontWeightSemiBold"
                color="text.primary"
                sx={{ lineHeight: 1.35 }}
              >
                {currentError.error.name}
              </Typography>
              {/* status · severity · what kind · how big · how recent */}
              <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
                {/* The issue's own status, then its fix's (RSI PRD fix-status chip). */}
                <ErrorStatusChip status={currentError.status} />
                <IssueStatusChip issue={currentError} />
                <ErrorSeverityBadge severity={currentError.severity} />
                <Typography typography="s2" color="text.secondary" sx={{ ml: 0.5 }}>
                  {[
                    errorTypeLabel(currentError.error.type),
                    currentError.trace_count != null && `${currentError.trace_count.toLocaleString()} traces`,
                    currentError.users_affected != null && `${currentError.users_affected.toLocaleString()} users`,
                    currentError.last_seen && `last seen ${agoLabel(currentError.last_seen)}`,
                  ].filter(Boolean).join(" · ")}
                </Typography>
              </Stack>
              {/* The Observe project it comes from, and the agent that project traces. */}
              <IssueProjectLine issue={currentError} />
            </Stack>

            <Stack direction="row" alignItems="center" gap={0.75} flexShrink={0}>
              {/* Triage — Resolve, Acknowledge, Ignore — in one menu; the
                  right rail's Status says the same thing at a glance. */}
              <Button
                size="small"
                variant="outlined"
                onClick={(e) => setTriageAnchor(e.currentTarget)}
                endIcon={<Iconify icon="eva:chevron-down-fill" width={14} />}
                disabled={updateIssue.isPending}
                sx={{
                  height: 30,
                  fontSize: "12px",
                  borderRadius: "6px",
                  textTransform: "none",
                  borderColor: "divider",
                  color: "text.primary",
                }}
              >
                Triage
              </Button>
              <Menu
                anchorEl={triageAnchor}
                open={!!triageAnchor}
                onClose={() => setTriageAnchor(null)}
                anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
                transformOrigin={{ vertical: "top", horizontal: "right" }}
                slotProps={{ paper: { sx: { width: 280, mt: 0.5 } } }}
              >
                {[
                  { key: "acknowledged", icon: "mdi:check-circle-outline", label: "Acknowledge", note: "", disabled: currentError?.status === "acknowledged" },
                  { key: "for_review", icon: "mdi:eye-outline", label: "Mark for review", note: "", disabled: currentError?.status === "for_review" },
                  { key: "resolved", icon: "mdi:check", label: "Resolve", note: triageFixEffect(fixStatus, "resolved")?.note || "", disabled: currentError?.status === "resolved" },
                ].map((t) => (
                  <MenuItem
                    key={t.key}
                    disabled={t.disabled}
                    onClick={() => {
                      setTriageAnchor(null);
                      updateIssue.mutate({ clusterId: currentError.cluster_id, status: t.key });
                      const effect = triageFixEffect(fixStatus, t.key);
                      if (effect) setReopened(effect.reopen);
                    }}
                    sx={{ alignItems: "flex-start", gap: 1.25, py: 1 }}
                  >
                    <Iconify icon={t.icon} width={15} sx={{ mt: "2px", color: "text.secondary", flexShrink: 0 }} />
                    <Box minWidth={0}>
                      <Typography sx={{ fontSize: "13px", fontWeight: 500 }}>{t.label}</Typography>
                      {t.note && (
                        <Typography sx={{ fontSize: "11.5px", color: "text.secondary", whiteSpace: "normal" }}>{t.note}</Typography>
                      )}
                    </Box>
                  </MenuItem>
                ))}
              </Menu>
              {/* Hand the issue to a coding agent instead. */}
              <IssueBriefButton
                issue={currentError}
                agent={fixAgent}
                sx={{ height: 30, borderRadius: "6px" }}
              />
              {/* Fix: prove a change in simulation, ship it as a draft. A
                  coding agent can join from the sheet. */}
              <FixButton issue={currentError} onOpenSheet={openFix} />
            </Stack>
          </Stack>
        </Box>

        {/* A fix that didn't hold — linked to the change. */}
        <ReopenedBanner issue={currentError} onFixAgain={openFix} />
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
            value={safeTabIndex}
            onChange={(_, v) => setActiveTab(TABS[v].key)}
            sx={{
              minHeight: 34,
              "& .MuiTab-root": {
                fontSize: "12px",
                minHeight: 34,
                py: 0,
                px: 0,
                mr: "20px",
                textTransform: "none",
                fontWeight: 500,
                color: "text.secondary",
                "&.Mui-selected": { color: "text.primary", fontWeight: 600 },
              },
              "& .MuiTabs-indicator": {
                height: 2,
                borderRadius: "2px 2px 0 0",
              },
            }}
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

      <LinearTeamPicker
        open={linearPickerOpen}
        onClose={() => setLinearPickerOpen(false)}
        clusterId={currentError?.cluster_id}
        traceId={null}
      />
    </Box>
  );
}
