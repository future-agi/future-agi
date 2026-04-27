import React, { useMemo, useCallback, useEffect, startTransition } from "react";
import PropTypes from "prop-types";
import { Box, Paper, useTheme, CircularProgress, Alert } from "@mui/material";
import { Outlet, useLocation, useNavigate, useParams } from "react-router";

import { useObserveHeader } from "../project/context/ObserveHeaderContext";
import { useUrlState } from "src/routes/hooks/use-url-state";

import ObserveHeader from "./ObserveHeader";
import {
  ObserveTabBar,
  ViewConfigModal,
  TabContextMenu,
} from "src/components/observe-tabs";
import ObserveTabs from "./ObserveTabs";
import { useTabStoreShallow } from "./LLMTracing/tabStore";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { useQueryClient } from "@tanstack/react-query";
import {
  useGetSavedViews,
  SAVED_VIEWS_KEY,
} from "src/api/project/saved-views";
import ReplayDrawer from "./ReplayDrawer/ReplayDrawer";
import {
  resetReplaySessionsStore,
  resetSessionsGridStore,
} from "./SessionsView/ReplaySessions/store";
import { resetTraceGridStore } from "./LLMTracing/states";
import { resetTabStore } from "./LLMTracing/tabStore";

// Loading component for tab content
const TabContentLoader = () => (
  <Box
    sx={{
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      height: "200px",
      backgroundColor: "background.paper",
    }}
  >
    <CircularProgress />
  </Box>
);

// Error boundary component
const TabErrorBoundary = ({ children }) => {
  return (
    <React.Suspense fallback={<TabContentLoader />}>{children}</React.Suspense>
  );
};

TabErrorBoundary.propTypes = {
  children: PropTypes.node.isRequired,
};

// Routes that use the new tab system (ObserveTabBar)
const TAB_SYSTEM_ROUTES = ["llm-tracing", "sessions", "users"];

// Map observe tab keys to route + URL params
const TAB_TO_ROUTE = {
  traces: { route: "llm-tracing", params: { selectedTab: "trace" } },
  sessions: { route: "sessions", params: {} },
  users: { route: "users", params: {} },
};

const ObservePage = React.memo(() => {
  const { headerConfig, setActiveViewConfig } = useObserveHeader();
  const theme = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const { observeId } = useParams();
  const { data: projectDetail } = useGetProjectDetails(observeId);
  const { data: savedViewsData } = useGetSavedViews(observeId);
  const queryClient = useQueryClient();

  // Tab store state for modals and context menu
  const {
    createModalOpen,
    editModalView,
    contextMenuAnchor,
    closeCreateModal,
    closeContextMenu,
    startRenaming,
  } = useTabStoreShallow((s) => ({
    createModalOpen: s.createModalOpen,
    editModalView: s.editModalView,
    contextMenuAnchor: s.contextMenuAnchor,
    closeCreateModal: s.closeCreateModal,
    closeContextMenu: s.closeContextMenu,
    startRenaming: s.startRenaming,
  }));

  // Active tab for the new tab system
  const [activeTab, setActiveTab] = useUrlState("tab", "traces");

  // Determine if current route uses the new tab system
  const currentRouteSegment = useMemo(() => {
    const segments = location.pathname.split("/").filter(Boolean);
    return segments[segments.length - 1] || "llm-tracing";
  }, [location.pathname]);

  const isTabSystemRoute = TAB_SYSTEM_ROUTES.includes(currentRouteSegment);

  // Derive active tab from URL on initial load / route changes
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const tab = params.get("tab");
    // Saved-view tabs own the `tab` key — don't overwrite with the route
    // default. Without this guard, a click on a sessions/users saved view
    // would land on the route, this effect would fire, and `tab=view-<id>`
    // would be replaced by `tab=sessions` (or `tab=users`), wiping the
    // active-view-id needed by Save view.
    if (tab && tab.startsWith("view-")) return;
    if (currentRouteSegment === "sessions") {
      setActiveTab("sessions");
    } else if (currentRouteSegment === "users") {
      setActiveTab("users");
    } else if (currentRouteSegment === "llm-tracing") {
      const selectedTab = params.get("selectedTab");
      if (selectedTab === "spans") {
        setActiveTab("spans");
      } else if (!tab || tab === "traces") {
        setActiveTab("traces");
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRouteSegment]);

  // Handle tab change from ObserveTabBar
  const handleTabChange = useCallback(
    (tabKey) => {
      // setActiveTab is redundant here: navigate() below updates the URL and
      // useUrlState's external-sync effect will update the state. Skipping
      // the explicit call saves one setSearchParams round and the
      // corresponding re-render cascade.

      // Set activeViewConfig from saved view data. Read from queryClient
      // directly rather than the `savedViewsData` closure, which can be stale
      // immediately after an optimistic cache write.
      let activeConfig = null;
      let viewTabType = "traces";
      if (tabKey.startsWith("view-")) {
        const viewId = tabKey.replace("view-", "");
        const cached = queryClient.getQueryData([SAVED_VIEWS_KEY, observeId]);
        const cachedResult = cached?.data?.result;
        const customViews =
          cachedResult?.customViews ?? cachedResult?.custom_views ?? [];
        const view = customViews.find((v) => v.id === viewId);
        activeConfig = view?.config || null;
        viewTabType = view?.tab_type ?? view?.tabType ?? "traces";
      }

      // Apply effects (activeViewConfig → apply effect → many setters) aren't
      // urgent for tab responsiveness. Defer via startTransition so the
      // navigation and URL update feel snappy while the filter apply runs as
      // a non-blocking transition.
      startTransition(() => {
        setActiveViewConfig(activeConfig);
      });

      // Navigate to the appropriate route
      if (tabKey.startsWith("view-")) {
        const isUsersView =
          viewTabType === "users" || viewTabType === "user_detail";
        const isSessionsView = viewTabType === "sessions";
        let routeSegment = "llm-tracing";
        if (isUsersView) routeSegment = "users";
        else if (isSessionsView) routeSegment = "sessions";
        const basePath = `/dashboard/observe/${observeId}/${routeSegment}`;

        const params = new URLSearchParams();
        params.set("tab", tabKey);

        if (isUsersView) {
          // Users-typed views use a different config schema (config.filters is
          // an object, not an array) and its own URL-state key pair.
          if (activeConfig?.filters?.dateFilter) {
            params.set(
              "userDateFilter",
              JSON.stringify(activeConfig.filters.dateFilter),
            );
          }
        } else if (isSessionsView) {
          // Sessions uses sessionFilter / sessionDateFilter URL keys.
          if (activeConfig?.filters) {
            params.set(
              "sessionFilter",
              JSON.stringify(activeConfig.filters),
            );
          }
          if (activeConfig?.display?.dateFilter) {
            params.set(
              "sessionDateFilter",
              JSON.stringify(activeConfig.display.dateFilter),
            );
          }
        } else {
          // Trace / Span views — pick URL keys that match LLMTracingView's
          // useLLMTracingFilters registrations.
          const isSpans = viewTabType === "spans";
          const selectedTabValue = isSpans ? "spans" : "trace";
          const primaryFilterKey = isSpans
            ? "primarySpanFilter"
            : "primaryTraceFilter";
          const primaryDateKey = isSpans
            ? "primarySpanDateFilter"
            : "primaryTraceDateFilter";
          const compareFilterKey = isSpans
            ? "compareSpansFilter"
            : "compareTraceFilter";
          const compareDateKey = isSpans
            ? "compareSpansDateFilter"
            : "compareTraceDateFilter";

          params.set("selectedTab", selectedTabValue);
          if (activeConfig?.filters) {
            params.set(primaryFilterKey, JSON.stringify(activeConfig.filters));
          }
          if (activeConfig?.display?.dateFilter) {
            params.set(
              primaryDateKey,
              JSON.stringify(activeConfig.display.dateFilter),
            );
          }
          if (activeConfig?.compareFilters) {
            params.set(
              compareFilterKey,
              JSON.stringify(activeConfig.compareFilters),
            );
          }
          if (activeConfig?.compareDateFilter) {
            params.set(
              compareDateKey,
              JSON.stringify(activeConfig.compareDateFilter),
            );
          }
        }

        navigate(`${basePath}?${params.toString()}`, {
          replace: true,
        });
      } else {
        const config = TAB_TO_ROUTE[tabKey];
        if (config) {
          const basePath = `/dashboard/observe/${observeId}/${config.route}`;
          const params = new URLSearchParams();
          params.set("tab", tabKey);
          Object.entries(config.params).forEach(([k, v]) => params.set(k, v));
          navigate(`${basePath}?${params.toString()}`, { replace: true });
        }
      }
    },
    [observeId, navigate, queryClient, setActiveViewConfig],
  );

  // Legacy tabs for non-tab-system routes (sessions, evals, charts, etc.)
  const legacyTabs = useMemo(
    () => [
      {
        id: "llm-tracing",
        title: "LLM Tracing",
        path: `/dashboard/observe/${observeId}/llm-tracing`,
        show: true,
      },
      {
        id: "evals-tasks",
        title: "Evals & Tasks",
        path: `/dashboard/observe/${observeId}/evals-tasks`,
        show: true,
      },
      {
        id: "charts",
        title: "Charts",
        path: `/dashboard/observe/${observeId}/charts`,
        show: true,
      },
      {
        id: "alerts",
        title: "Alerts",
        path: `/dashboard/observe/${observeId}/alerts`,
        show: true,
      },
    ],
    [observeId],
  );

  const currentLegacyTab = useMemo(() => {
    const segments = location.pathname.split("/").filter(Boolean);
    return legacyTabs.find((tab) => segments.includes(tab.id)) || legacyTabs[0];
  }, [location.pathname, legacyTabs]);

  const handleLegacyTabChange = useCallback(
    (event, newTabId) => {
      const selectedTab = legacyTabs.find((tab) => tab.id === newTabId);
      if (selectedTab && selectedTab.path !== location.pathname) {
        navigate(selectedTab.path, { replace: true });
      }
    },
    [legacyTabs, location.pathname, navigate],
  );

  // Memoized styles
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

  useEffect(() => {
    return () => {
      resetReplaySessionsStore();
      resetSessionsGridStore();
      resetTraceGridStore();
      resetTabStore();
      headerConfig?.gridApi?.deselectAll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [observeId]);

  if (!observeId) {
    return (
      <Box sx={{ p: 3, backgroundColor: "background.paper" }}>
        <Alert severity="error">
          Invalid observe ID. Please check the URL and try again.
        </Alert>
      </Box>
    );
  }

  return (
    <Box sx={containerStyles}>
      {/* Header Section */}
      <Paper sx={headerPaperStyles}>
        <ObserveHeader
          text={headerConfig.text}
          filterTrace={headerConfig.filterTrace}
          filterSpan={headerConfig.filterSpan}
          selectedTab={headerConfig.selectedTab}
          filterSession={headerConfig.filterSession}
          refreshData={headerConfig.refreshData}
          resetFilters={headerConfig.resetFilters}
        />
      </Paper>

      {/* Tabs Section */}
      <Paper sx={tabsPaperStyles}>
        {isTabSystemRoute ? (
          <ObserveTabBar
            projectId={observeId}
            activeTab={activeTab}
            onTabChange={handleTabChange}
            projectSource={projectDetail?.source}
          />
        ) : (
          <ObserveTabs
            tabs={legacyTabs}
            currentTab={currentLegacyTab}
            onTabChange={handleLegacyTabChange}
            observeId={observeId}
          />
        )}
      </Paper>

      {/* Filter chips slot — FilterChips portals here */}
      <Box
        id="observe-filter-chips-slot"
        sx={{ px: 2, flexShrink: 0, bgcolor: "background.paper" }}
      />

      {/* Content Section */}
      <Box sx={contentStyles}>
        <TabErrorBoundary>
          <Outlet />
        </TabErrorBoundary>
      </Box>
      <ReplayDrawer
        gridApi={headerConfig?.gridApi}
        currentTab={currentLegacyTab}
        projectDetail={projectDetail}
      />

      {/* View config modal (create / edit) */}
      <ViewConfigModal
        open={createModalOpen}
        onClose={closeCreateModal}
        mode={editModalView ? "edit" : "create"}
        initialValues={editModalView}
        projectId={observeId}
        onSuccess={(newView) => {
          if (newView?.id) {
            handleTabChange(`view-${newView.id}`);
          }
        }}
      />

      {/* Tab context menu (right-click on custom view tabs) */}
      {contextMenuAnchor && (
        <TabContextMenu
          anchorPosition={contextMenuAnchor}
          view={
            (savedViewsData?.customViews ?? savedViewsData?.custom_views)?.find(
              (v) => v.id === contextMenuAnchor.viewId,
            ) ?? null
          }
          projectId={observeId}
          onClose={closeContextMenu}
          onRename={startRenaming}
          onTabChange={handleTabChange}
        />
      )}
    </Box>
  );
});

ObservePage.displayName = "ObservePage";

export default ObservePage;
