import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, Divider, Paper, Typography, useTheme } from "@mui/material";
import { Helmet } from "react-helmet-async";
import { useNavigate, useParams } from "react-router";
import Iconify from "src/components/iconify";
import { useUrlState } from "src/routes/hooks/use-url-state";

import ObserveHeaderProvider from "src/sections/project/context/ObserveHeaderContextProvider";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";
import { useGetWorkspaceSavedViews } from "src/api/project/saved-views";

import LLMTracingView from "../../LLMTracing/LLMTracingView";
import SessionsView from "../../SessionsView/Sessions-view";
import UserDetailTabBar from "./UserDetailTabBar";

const USER_DETAIL_TAB_TYPE = "user_detail";

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

  const handleTabChange = useCallback(
    (nextTabKey, nextSubTab) => {
      setActiveTab(nextTabKey);
      if (nextSubTab && nextSubTab !== subTab) setSubTab(nextSubTab);
    },
    [setActiveTab, subTab],
  );

  // Hydrate activeViewConfig + the rendered subTab on hard-refresh / direct
  // URL load. UserDetailTabBar already reacts to activeTab changes via click,
  // but a page reload mounts the heavy sub-view (SessionsView / LLMTracingView)
  // before the tab bar's effect lands — leaving filters / column visibility /
  // extraFilters stuck at defaults until the user clicks the tab again.
  // Mirrors ObservePage's hydration pattern. Re-runs only when the URL tab
  // key or the saved-views list changes; the value for `userTab=view-<id>`
  // is stable across saved-views refetches so the apply effect doesn't
  // churn on every mutation invalidate.
  const { setActiveViewConfig } = useObserveHeader();
  const { data: workspaceSavedViewsData } =
    useGetWorkspaceSavedViews(USER_DETAIL_TAB_TYPE);
  const lastHydratedTabRef = useRef(null);
  useEffect(() => {
    if (!activeTab || !activeTab.startsWith("view-")) {
      lastHydratedTabRef.current = null;
      return;
    }
    if (lastHydratedTabRef.current === activeTab) return;
    const customViews =
      workspaceSavedViewsData?.customViews ??
      workspaceSavedViewsData?.custom_views ??
      [];
    if (!customViews.length) return;
    const viewId = activeTab.slice(5);
    const view = customViews.find((v) => v.id === viewId);
    if (!view?.config) return;
    lastHydratedTabRef.current = activeTab;
    setActiveViewConfig(view.config);
    const targetSubTab = view.config.sub_tab || view.config.subTab;
    if (targetSubTab && targetSubTab !== subTab) setSubTab(targetSubTab);
  }, [activeTab, workspaceSavedViewsData, setActiveViewConfig, subTab]);

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
