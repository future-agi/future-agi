import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Box,
  Button,
  Divider,
  Paper,
  Typography,
  useTheme,
} from "@mui/material";
import { Helmet } from "react-helmet-async";
import {
  useLocation,
  useNavigate,
  useNavigationType,
  useParams,
} from "react-router";
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
      // Sync subTab to userTab — the group dropdown navigates the URL directly,
      // not via handleTabChange, so otherwise the view wouldn't switch.
      if (activeTab === "sessions" || activeTab === "traces") {
        if (activeTab !== subTab) setSubTab(activeTab);
      }
      return;
    }
    if (lastHydratedTabRef.current === activeTab) return;
    const customViews = workspaceSavedViewsData?.custom_views ?? [];
    if (!customViews.length) return;
    const viewId = activeTab.slice(5);
    const view = customViews.find((v) => v.id === viewId);
    if (!view?.config) return;
    lastHydratedTabRef.current = activeTab;
    setActiveViewConfig(view.config);
    const targetSubTab = view.config.sub_tab;
    if (targetSubTab && targetSubTab !== subTab) setSubTab(targetSubTab);
  }, [activeTab, workspaceSavedViewsData, setActiveViewConfig, subTab]);

  // Back returns to the page the user came from, with its URL state (project
  // Users tab, saved-view tab, date range). The views below push their own
  // entries (group-by switch, compare traces), so step back past every entry
  // since this page opened, not just one. The page counts those entries
  // itself, by router location key. The URL-synced drawer setters
  // (LLMTracing/states.js) keep the router's history.state, so keys survive
  // them; history.state.idx is still not trusted, because any other script
  // that replaces history.state with {} erases it and the router numbers
  // later pushes NaN. Opened
  // by a push, the page has its origin behind it; a direct link has nothing
  // behind it, so Back falls back to the all-projects Users list.
  const location = useLocation();
  const navigationType = useNavigationType();
  const [hasOrigin] = useState(
    () => navigationType === "PUSH" || window.history.state?.idx > 0,
  );
  const pageHistoryRef = useRef({
    keys: [location.key],
    urls: [location.pathname + location.search],
    at: 0,
    pushed: navigationType === "PUSH",
    location,
  });
  useEffect(() => {
    const page = pageHistoryRef.current;
    if (page.location === location) return;
    page.location = location;
    const url = location.pathname + location.search;
    if (navigationType === "PUSH") {
      page.at += 1;
      page.keys = page.keys.slice(0, page.at);
      page.urls = page.urls.slice(0, page.at);
    } else if (navigationType === "POP") {
      // An entry whose history.state something replaced with {} reads back
      // with key "default", so the page cannot find it by key. As a fallback
      // it works out which way the browser went
      // instead: from the entry it was pushed into, a step back would have
      // left the page, so it went forward; further in, the URL tells the two
      // neighbors apart. When it cannot tell, it counts a step back, so Back
      // stops short of the origin rather than going past it.
      const known =
        location.key === "default" ? -1 : page.keys.indexOf(location.key);
      const forward =
        (page.at === 0 && page.pushed) ||
        (page.urls[page.at + 1] === url && page.urls[page.at - 1] !== url);
      if (known !== -1) page.at = known;
      else page.at = forward ? page.at + 1 : Math.max(page.at - 1, 0);
    }
    // A pop never renames an entry the page already holds.
    if (navigationType !== "POP" || page.at >= page.keys.length) {
      page.keys[page.at] = location.key;
      page.urls[page.at] = url;
    }
  }, [location, navigationType]);
  const handleBack = useCallback(() => {
    if (hasOrigin) navigate(-(pageHistoryRef.current.at + 1));
    else navigate("/dashboard/users");
  }, [hasOrigin, navigate]);

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
                <Button
                  variant="outlined"
                  size="small"
                  onClick={handleBack}
                  startIcon={
                    <Iconify icon="solar:alt-arrow-left-linear" width={16} />
                  }
                >
                  Back
                </Button>

                <Box
                  sx={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.75,
                    maxWidth: 480,
                  }}
                >
                  <Iconify
                    icon="solar:user-linear"
                    width={16}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography
                    noWrap
                    sx={{
                      typography: "m1",
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
