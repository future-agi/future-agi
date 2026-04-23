import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Box, Divider, Paper, Typography, useTheme } from "@mui/material";
import { Helmet } from "react-helmet-async";
import { useNavigate, useParams } from "react-router";
import Iconify from "src/components/iconify";
import { useUrlState } from "src/routes/hooks/use-url-state";

import ObserveHeaderProvider from "src/sections/project/context/ObserveHeaderContextProvider";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";

import LLMTracingView from "../../LLMTracing/LLMTracingView";
import SessionsView from "../../SessionsView/Sessions-view";
import UserDetailTabBar from "./UserDetailTabBar";

// Cross-project user detail page.
// ---------------------------------------------------------------------------
// Mounted at /dashboard/users/:userId.
//
// Mirrors ObservePage's chrome (same Paper/padding/divider layout) so it feels
// like a first-class observe page. Renders the actual LLMTracingView /
// SessionsView components in `mode="user"` — no clones, full feature parity
// (display panel, custom columns, filters, drawer).
//
// Wrapped in ObserveHeaderProvider so LLMTracingView's existing activeViewConfig
// restore logic works for workspace-scoped user-detail saved views.

const CrossProjectUserDetailPage = () => (
  <ObserveHeaderProvider>
    <UserDetailPageBody />
  </ObserveHeaderProvider>
);

const TRACE_DISPLAY_KEY = (uid) => `user-display-${uid}`;
const TRACE_FILTERS_KEY = (uid) => `user-filters-${uid}`;

const UserDetailPageBody = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { userId } = useParams();
  // `activeTab` is the tab-bar entry — "sessions", "traces", or "view-<id>".
  // `subTab` is the rendered sub-view ("sessions" | "traces"). Only one of
  // the two heavy views is mounted at a time to avoid ObserveToolbar portal
  // collisions inside the #observe-toolbar-slot.
  const [activeTab, setActiveTab] = useUrlState("userTab", "sessions");
  const [subTab, setSubTab] = useState(
    activeTab === "traces" ? "traces" : "sessions",
  );

  // Keep the mounted sub-view in sync with the URL for the two simple
  // keys ("traces" / "sessions") so group-by navigation from inside the
  // heavy views lands on the right one. Saved-view keys ("view-<id>")
  // are handled by handleTabChange which sets both activeTab and subTab
  // together — we skip them here so this effect never clobbers that.
  useEffect(() => {
    if (activeTab !== "traces" && activeTab !== "sessions") return;
    setSubTab((prev) => (prev === activeTab ? prev : activeTab));
  }, [activeTab]);

  const { setActiveViewConfig } = useObserveHeader();

  // -------------------------------------------------------------------------
  // Saved-view capture + apply
  // -------------------------------------------------------------------------
  // Capture reads the inner view's current state:
  //   - traces: namespaced localStorage (LLMTracingView already persists there)
  //   - sessions: v1 only stores {sub_tab} — Sessions-view has no config
  //     restore path yet (driven by URL state, not context)
  const getConfigFor = useCallback(
    (which) => {
      if (which !== "traces") return {};
      try {
        const rawDisplay = localStorage.getItem(TRACE_DISPLAY_KEY(userId));
        const rawFilters = localStorage.getItem(TRACE_FILTERS_KEY(userId));
        const display = rawDisplay ? JSON.parse(rawDisplay) : null;
        const filtersBlob = rawFilters ? JSON.parse(rawFilters) : null;
        // LLMTracingView consumes activeViewConfig with shape
        // { display, filters, extraFilters, compareFilters, ... }. Pass
        // through what exists; missing keys are ignored by the consumer.
        return {
          ...(display ? { display } : {}),
          ...(filtersBlob?.filters ? { filters: filtersBlob.filters } : {}),
          ...(filtersBlob?.extraFilters
            ? { extraFilters: filtersBlob.extraFilters }
            : {}),
          ...(filtersBlob?.dateFilter
            ? { dateFilter: filtersBlob.dateFilter }
            : {}),
        };
      } catch {
        return {};
      }
    },
    [userId],
  );

  // Apply:
  //   - traces: push config into the ObserveHeader context — LLMTracingView's
  //     existing `activeViewConfig` effect will restore display + filters
  //   - sessions: no-op in v1 (just flips the sub-tab via onTabChange)
  const applyConfigFor = useCallback(
    (which, cfg) => {
      if (which === "traces") {
        setActiveViewConfig(cfg || null);
      }
    },
    [setActiveViewConfig],
  );

  const handleTabChange = useCallback(
    (nextTabKey, nextSubTab) => {
      setActiveTab(nextTabKey);
      if (nextSubTab && nextSubTab !== subTab) setSubTab(nextSubTab);
    },
    [setActiveTab, subTab],
  );

  // Styles — match ObservePage exactly
  const containerStyles = useMemo(
    () => ({
      display: "flex",
      flexDirection: "column",
      height: "100vh",
      backgroundColor: "background.paper",
    }),
    [],
  );

  const headerPaperStyles = useMemo(
    () => ({
      paddingX: theme.spacing(2),
      paddingTop: theme.spacing(2),
      borderRadius: 0,
      boxShadow: "none",
      backgroundColor: "background.paper",
      flexShrink: 0,
    }),
    [theme],
  );

  const tabsPaperStyles = useMemo(
    () => ({
      paddingX: theme.spacing(2),
      paddingTop: theme.spacing(0.5),
      paddingBottom: theme.spacing(0.5),
      boxShadow: "none",
      backgroundColor: "background.paper",
      flexShrink: 0,
    }),
    [theme],
  );

  const contentStyles = useMemo(
    () => ({
      flex: 1,
      overflow: "auto",
      backgroundColor: "background.paper",
    }),
    [],
  );

  return (
    <>
      <Helmet>
        <title>{userId} - User Details</title>
      </Helmet>
      <Box sx={containerStyles}>
        {/* === Header === */}
        <Paper sx={headerPaperStyles}>
          <Box display="flex" flexDirection="column" width="100%">
            <Box
              display="flex"
              alignItems="center"
              justifyContent="space-between"
              sx={{ minHeight: 38 }}
            >
              <Box display="flex" alignItems="center" gap={1.5}>
                <Box
                  component="button"
                  onClick={() => navigate("/dashboard/users")}
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.5,
                    height: 26,
                    px: 1.5,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "4px",
                    bgcolor: "transparent",
                    cursor: "pointer",
                    fontSize: 14,
                    fontWeight: 500,
                    fontFamily: "'IBM Plex Sans', sans-serif",
                    color: "text.primary",
                    "&:hover": { bgcolor: "action.hover" },
                  }}
                >
                  <Iconify icon="mdi:chevron-left" width={16} />
                  Back
                </Box>

                <Box
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.75,
                    height: 26,
                    px: 1.5,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "4px",
                    bgcolor: "background.paper",
                    maxWidth: 480,
                  }}
                >
                  <Iconify
                    icon="mdi:account-outline"
                    width={16}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography
                    variant="body2"
                    noWrap
                    sx={{
                      fontWeight: 600,
                      color: "text.primary",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {userId}
                  </Typography>
                </Box>

                <Typography
                  variant="caption"
                  sx={{ color: "text.secondary", ml: 0.5 }}
                >
                  Activity across all projects
                </Typography>
              </Box>
            </Box>
            <Divider sx={{ mt: 1.5, borderColor: "divider" }} />
          </Box>
        </Paper>

        {/* === Tab bar === */}
        <Paper sx={tabsPaperStyles}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              minHeight: 36,
              gap: 1,
            }}
          >
            <UserDetailTabBar
              activeTab={activeTab}
              onTabChange={handleTabChange}
              getConfigFor={getConfigFor}
              applyConfigFor={applyConfigFor}
            />
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                flexShrink: 0,
              }}
            >
              <Box
                id="observe-toolbar-slot"
                sx={{ display: "flex", alignItems: "center", gap: 1 }}
              />
            </Box>
          </Box>
        </Paper>

        <Box
          id="observe-filter-chips-slot"
          sx={{ px: 2, flexShrink: 0, bgcolor: "background.paper" }}
        />

        <Box sx={contentStyles}>
          {subTab === "sessions" ? (
            <SessionsView mode="user" userIdForUserMode={userId} />
          ) : (
            <LLMTracingView mode="user" userIdForUserMode={userId} />
          )}
        </Box>
      </Box>
    </>
  );
};

export default CrossProjectUserDetailPage;
