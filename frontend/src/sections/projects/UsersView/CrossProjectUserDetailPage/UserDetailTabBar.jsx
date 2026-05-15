import React, {
  useCallback,
  useMemo,
  useState,
  useEffect,
  useRef,
  startTransition,
} from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Divider } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import FixedTab from "src/components/observe-tabs/FixedTab";
import CustomViewTab from "src/components/observe-tabs/CustomViewTab";
import SaveViewPopover from "src/components/traceDetail/SaveViewDialog";
import { useQueryClient } from "@tanstack/react-query";
import {
  useGetWorkspaceSavedViews,
  useCreateWorkspaceSavedView,
  useUpdateWorkspaceSavedView,
  useDeleteWorkspaceSavedView,
  SAVED_VIEWS_KEY,
} from "src/api/project/saved-views";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";
import { useSearchParams } from "react-router-dom";

const USER_DETAIL_TAB_TYPE = "user_detail";

const FIXED_TABS = [
  {
    key: "sessions",
    label: "Sessions",
    icon: "mdi:account-group-outline",
    shortcut: "1",
  },
  {
    key: "traces",
    label: "Trace",
    icon: "mdi:link-variant",
    shortcut: "2",
  },
];

// Cross-project user-detail page tab bar.
// - Fixed tabs: Sessions, Traces
// - Custom workspace-saved views (personal) whose config captures the active
//   subTab + filter/display state so clicking one jumps to that subTab and
//   restores its config via the parent's imperative api refs.
const UserDetailTabBar = ({ activeTab, onTabChange }) => {
  const { data: savedViewsData } =
    useGetWorkspaceSavedViews(USER_DETAIL_TAB_TYPE);
  const customViews = useMemo(
    () => savedViewsData?.customViews ?? savedViewsData?.custom_views ?? [],
    [savedViewsData],
  );

  const queryClient = useQueryClient();
  const { getViewConfig, setActiveViewConfig } = useObserveHeader();
  const [, setSearchParams] = useSearchParams();

  // Pre-seed the URL-synced state keys for the target sub-tab from the saved
  // view's config. Lets useUrlState read correct values on mount / refresh so
  // the sub-view doesn't do a double-fetch (defaults then saved).
  const seedUrlForView = useCallback(
    (subTab, config) => {
      if (!config) return;
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          const display = config.display || {};
          if (subTab === "sessions") {
            if (display.dateFilter) {
              next.set("sessionDateFilter", JSON.stringify(display.dateFilter));
            }
            if (display.cellHeight) {
              next.set("sessionCellHeight", JSON.stringify(display.cellHeight));
            }
            if (display.showCompare !== undefined) {
              next.set(
                "sessionShowCompare",
                JSON.stringify(display.showCompare),
              );
            }
          } else if (subTab === "traces") {
            if (display.dateFilter) {
              next.set(
                "primaryTraceDateFilter",
                JSON.stringify(display.dateFilter),
              );
            }
            if (config.filters) {
              next.set("primaryTraceFilter", JSON.stringify(config.filters));
            }
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const { mutate: createSavedView } =
    useCreateWorkspaceSavedView(USER_DETAIL_TAB_TYPE);
  const { mutate: updateSavedView } =
    useUpdateWorkspaceSavedView(USER_DETAIL_TAB_TYPE);
  const { mutate: deleteSavedView } =
    useDeleteWorkspaceSavedView(USER_DETAIL_TAB_TYPE);

  const [saveViewAnchor, setSaveViewAnchor] = useState(null);
  const [isSavingView, setIsSavingView] = useState(false);
  const [renamingId, setRenamingId] = useState(null);

  // Apply config only on real tab transitions. Read from the React Query
  // cache directly to avoid a stale-closure race right after create — the
  // mutation's optimistic setQueryData has the new view before the
  // `customViews` prop closure re-renders.
  const lastAppliedTabRef = useRef(null);

  useEffect(() => {
    if (lastAppliedTabRef.current === activeTab) return;

    // Fixed tab (sessions/traces): hard-reset the URL down to just `userTab`
    // so any filter/date/display params left over from a previous saved view
    // are wiped. useUrlState's external-sync then resets each sub-view's
    // state to its default.
    //
    // The FixedTab onClick handler below already runs this reset
    // synchronously in the click event tick — that's the primary path and
    // is what guarantees `setActiveViewConfig(null)` lands in the same
    // commit as the subTab swap (a `useEffect` reset would be one commit
    // too late and the newly-mounted sub-view would read the stale custom
    // config). This branch survives as a safety net for non-click activeTab
    // transitions, e.g. handleClose at line ~230 calling
    // `onTabChange("sessions","sessions")` after deleting an active view.
    if (FIXED_TABS.some((t) => t.key === activeTab)) {
      setSearchParams(new URLSearchParams({ userTab: activeTab }), {
        replace: true,
      });
      lastAppliedTabRef.current = activeTab;
      setActiveViewConfig(null);
      return;
    }

    const id = activeTab?.startsWith?.("view-") ? activeTab.slice(5) : null;
    if (!id) {
      lastAppliedTabRef.current = activeTab;
      return;
    }
    const cached = queryClient.getQueryData([
      SAVED_VIEWS_KEY,
      "workspace",
      USER_DETAIL_TAB_TYPE,
    ]);
    const cachedResult = cached?.data?.result;
    const cachedList =
      cachedResult?.customViews ?? cachedResult?.custom_views ?? [];
    const view =
      cachedList.find((v) => v.id === id) ??
      customViews.find((v) => v.id === id);
    if (view?.config) {
      const subTab = view.config.sub_tab || view.config.subTab || "sessions";
      // Seed URL state first so the sub-view can read correct filter/date
      // values on its first render, avoiding a default-then-saved double fetch
      // (the flash). Apply via setActiveViewConfig still handles non-URL
      // state (extraFilters, display.cellHeight, etc.).
      seedUrlForView(subTab, view.config);
      onTabChange?.(activeTab, subTab);
      startTransition(() => setActiveViewConfig(view.config));
      lastAppliedTabRef.current = activeTab;
    }
    // If not found yet — retry once customViews dep changes (React Query
    // refetch will land and customViews will include the new view).
  }, [
    activeTab,
    setActiveViewConfig,
    onTabChange,
    customViews,
    queryClient,
    seedUrlForView,
  ]);

  const handleSaveViewConfirm = useCallback(
    (name) => {
      setIsSavingView(true);
      // We save the currently-active sub-tab's config. activeTab may itself
      // be a custom view — in that case fall back to its underlying sub_tab.
      let targetSubTab = null;
      if (FIXED_TABS.some((t) => t.key === activeTab)) {
        targetSubTab = activeTab;
      } else {
        const id = activeTab?.startsWith?.("view-") ? activeTab.slice(5) : null;
        const view = id ? customViews.find((v) => v.id === id) : null;
        targetSubTab =
          view?.config?.sub_tab || view?.config?.subTab || "sessions";
      }
      // Live snapshot of the currently-mounted sub-view (LLMTracingView /
      // SessionsView register their buildViewConfig on the shared
      // ObserveHeaderContext).
      const inner = getViewConfig?.() ?? {};
      const config = { ...inner, sub_tab: targetSubTab };
      createSavedView(
        { name, config },
        {
          onSuccess: (res) => {
            enqueueSnackbar("View created", { variant: "success" });
            const newId = res?.data?.result?.id;
            if (newId) onTabChange?.(`view-${newId}`, targetSubTab);
            setSaveViewAnchor(null);
            setIsSavingView(false);
          },
          onError: () => {
            enqueueSnackbar("Failed to create view", { variant: "error" });
            setIsSavingView(false);
          },
        },
      );
    },
    [activeTab, customViews, createSavedView, getViewConfig, onTabChange],
  );

  const handleClose = useCallback(
    (viewId) => {
      deleteSavedView(viewId, {
        onSuccess: () => {
          if (activeTab === `view-${viewId}`) {
            onTabChange?.("sessions", "sessions");
          }
        },
      });
    },
    [deleteSavedView, activeTab, onTabChange],
  );

  const handleRenameSubmit = useCallback(
    (viewId, newName) => {
      updateSavedView({ id: viewId, name: newName });
      setRenamingId(null);
    },
    [updateSavedView],
  );

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        minHeight: 36,
        gap: 1,
        flex: 1,
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "4px",
          flexShrink: 0,
        }}
      >
        {FIXED_TABS.map((tab) => (
          <FixedTab
            key={tab.key}
            tabKey={tab.key}
            label={tab.label}
            icon={tab.icon}
            shortcut={tab.shortcut}
            isActive={activeTab === tab.key}
            onClick={(key) => {
              // Reset context + URL synchronously in the click event so they
              // batch with setActiveTab/setSubTab into one commit. Otherwise
              // the newly-mounted sub-view (e.g. LLMTracingView remounting on
              // sessions→traces) reads the stale custom-view config from
              // context during its first render and re-writes the saved
              // display state back into the URL before the apply useEffect
              // gets a chance to clear it.
              setActiveViewConfig(null);
              setSearchParams(new URLSearchParams({ userTab: key }), {
                replace: true,
              });
              onTabChange?.(key, key);
            }}
          />
        ))}
      </Box>

      {customViews.length > 0 && (
        <Divider
          orientation="vertical"
          flexItem
          sx={{ mx: 0.5, my: 1, borderColor: "divider", flexShrink: 0 }}
        />
      )}

      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "4px",
          flex: 1,
          minWidth: 0,
          overflowX: "auto",
          overflowY: "hidden",
          scrollbarWidth: "thin",
          "&::-webkit-scrollbar": { height: 6 },
          "&::-webkit-scrollbar-track": { bgcolor: "transparent" },
          "&::-webkit-scrollbar-thumb": {
            bgcolor: "divider",
            borderRadius: 3,
          },
        }}
      >
        {customViews.map((view, idx) => (
          <CustomViewTab
            key={`view-${view.id}`}
            view={view}
            shortcut={idx + 3 <= 9 ? String(idx + 3) : undefined}
            isActive={activeTab === `view-${view.id}`}
            isRenaming={renamingId === view.id}
            onClick={(key) => {
              const subTab =
                view.config?.sub_tab || view.config?.subTab || "sessions";
              // Seed URL synchronously in the same click tick so the tab
              // switch, URL params, and sub-view mount all happen in one
              // React commit. Without this the sub-view mounts first with
              // defaults, fetches, then the apply-effect seeds the URL and
              // triggers a second fetch (the flash).
              seedUrlForView(subTab, view.config);
              onTabChange?.(key, subTab);
            }}
            onClose={handleClose}
            onContextMenu={(x, y, id) => setRenamingId(id)}
            onRenameSubmit={handleRenameSubmit}
            onRenameCancel={() => setRenamingId(null)}
          />
        ))}
      </Box>

      <CustomTooltip
        show
        title="Save current view"
        placement="bottom"
        arrow
        size="small"
        type="black"
      >
        <ButtonBase
          data-create-view-btn
          onClick={(e) => setSaveViewAnchor(e.currentTarget)}
          sx={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            height: 26,
            width: 26,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            bgcolor: "background.paper",
            flexShrink: 0,
            "&:hover": { bgcolor: "background.neutral" },
          }}
        >
          <Iconify icon="mdi:plus" width={16} sx={{ color: "text.primary" }} />
        </ButtonBase>
      </CustomTooltip>

      <SaveViewPopover
        anchorEl={saveViewAnchor}
        open={Boolean(saveViewAnchor)}
        onClose={() => setSaveViewAnchor(null)}
        onSave={handleSaveViewConfirm}
        isLoading={isSavingView}
      />
    </Box>
  );
};

UserDetailTabBar.propTypes = {
  activeTab: PropTypes.string.isRequired,
  onTabChange: PropTypes.func.isRequired,
};

export default UserDetailTabBar;
