import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Divider } from "@mui/material";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  horizontalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import {
  useGetSavedViews,
  useUpdateSavedView,
  useDeleteSavedView,
  useReorderSavedViews,
} from "src/api/project/saved-views";
import { useTabStoreShallow } from "src/sections/projects/LLMTracing/tabStore";
import { useObserveHeader } from "src/sections/project/context/ObserveHeaderContext";

import FixedTab from "./FixedTab";
import CustomViewTab from "./CustomViewTab";
import SaveViewPopover from "src/components/traceDetail/SaveViewDialog";
import { useCreateSavedView } from "src/api/project/saved-views";
import { enqueueSnackbar } from "notistack";

// Fixed default tabs — matches backend DEFAULT_TABS
const FIXED_TABS = [
  { key: "traces", label: "Trace", icon: "mdi:link-variant", shortcut: "1" },
  {
    key: "sessions",
    label: "Sessions",
    icon: "mdi:account-group-outline",
    shortcut: "2",
  },
  { key: "users", label: "Users", icon: "mdi:account-outline", shortcut: "3" },
];

// ---------------------------------------------------------------------------
// Sortable wrapper for custom view tabs
// ---------------------------------------------------------------------------
const SortableViewTab = ({ tab, idx, ...tabProps }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: tab.view.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    flexShrink: 0,
    cursor: "grab",
  };

  return (
    <Box ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <CustomViewTab
        view={tab.view}
        shortcut={idx + 4 <= 9 ? String(idx + 4) : undefined}
        {...tabProps}
      />
    </Box>
  );
};

SortableViewTab.propTypes = {
  tab: PropTypes.object.isRequired,
  idx: PropTypes.number.isRequired,
};

// ---------------------------------------------------------------------------
// ObserveTabBar
// ---------------------------------------------------------------------------
const ObserveTabBar = ({
  projectId,
  activeTab,
  onTabChange,
  renderRight,
  projectSource,
}) => {
  // Hide Sessions/Users tabs for voice (simulator) projects
  const visibleFixedTabs = useMemo(() => {
    if (projectSource === "simulator") {
      return FIXED_TABS.filter((t) => t.key === "traces");
    }
    return FIXED_TABS;
  }, [projectSource]);
  const { data: savedViewsData } = useGetSavedViews(projectId);
  // Only show trace-list views (traces/spans/voice) — exclude "imagine" tabs (those belong to trace detail)
  const customViews = (
    savedViewsData?.customViews ??
    savedViewsData?.custom_views ??
    []
  ).filter((v) => (v.tab_type || v.tabType) !== "imagine");

  // Mutations
  const { mutate: updateView } = useUpdateSavedView(projectId);
  const { mutate: deleteView } = useDeleteSavedView(projectId);
  const { mutate: reorderViews } = useReorderSavedViews(projectId);

  const { isDirty, editingTabId, openContextMenu, stopRenaming } =
    useTabStoreShallow((s) => ({
      isDirty: s.isDirty,
      editingTabId: s.editingTabId,
      openContextMenu: s.openContextMenu,
      stopRenaming: s.stopRenaming,
    }));

  // Inline Save View popover (replaces ViewConfigModal)
  const [saveViewAnchor, setSaveViewAnchor] = useState(null);
  const [isSavingView, setIsSavingView] = useState(false);
  const { mutate: createSavedView } = useCreateSavedView(projectId);
  const { getViewConfig, getTabType } = useObserveHeader();

  // Derive tab_type for a new saved view. Priority:
  //  - on a saved view tab, inherit the view's tab_type.
  //  - on the Users fixed tab, save as "users".
  //  - on the Sessions fixed tab, save as "sessions".
  //  - on the Traces fixed tab, fall back to LLMTracingView's registered
  //    callback (distinguishes trace vs span sub-tab).
  const resolveTabType = useCallback(() => {
    if (activeTab === "users") return "users";
    if (activeTab === "sessions") return "sessions";
    if (activeTab === "traces") return getTabType?.() ?? "traces";
    if (activeTab?.startsWith("view-")) {
      const currentId = activeTab.replace("view-", "");
      const current = customViews.find((v) => v.id === currentId);
      return current?.tab_type ?? current?.tabType ?? "traces";
    }
    return "traces";
  }, [activeTab, customViews, getTabType]);

  const handleSaveViewConfirm = useCallback(
    (name) => {
      setIsSavingView(true);
      const snapshot = getViewConfig?.() ?? null;
      const tabType = resolveTabType();
      createSavedView(
        {
          project_id: projectId,
          name,
          tab_type: tabType,
          config: snapshot ?? {},
        },
        {
          onSuccess: (res) => {
            enqueueSnackbar("View created", { variant: "success" });
            const newId = res?.data?.result?.id;
            if (newId) onTabChange?.(`view-${newId}`);
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
    [projectId, createSavedView, onTabChange, getViewConfig, resolveTabType],
  );

  // DnD sensors — require 5px of movement before starting drag
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  // -----------------------------------------------------------------------
  // Build tab lists
  // -----------------------------------------------------------------------
  const allCustomTabs = useMemo(
    () =>
      customViews.map((v) => ({
        key: `view-${v.id}`,
        name: v.name,
        view: v,
      })),
    [customViews],
  );

  const sortableIds = allCustomTabs.map((t) => t.view.id);

  // -----------------------------------------------------------------------
  // DnD handler
  // -----------------------------------------------------------------------
  const handleDragEnd = useCallback(
    (event) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;

      const oldIndex = customViews.findIndex((v) => v.id === active.id);
      const newIndex = customViews.findIndex((v) => v.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return;

      // Build new order
      const reordered = [...customViews];
      const [moved] = reordered.splice(oldIndex, 1);
      reordered.splice(newIndex, 0, moved);

      const order = reordered.map((v, i) => ({ id: v.id, position: i }));
      reorderViews({ project_id: projectId, order });
    },
    [customViews, projectId, reorderViews],
  );

  // -----------------------------------------------------------------------
  // Keyboard shortcuts (1-9)
  // -----------------------------------------------------------------------
  useEffect(() => {
    const handleKeyDown = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const num = parseInt(e.key, 10);
      if (isNaN(num) || num < 1 || num > 9) return;

      if (num <= 3) {
        onTabChange(visibleFixedTabs[num - 1]?.key);
      } else {
        const idx = num - 4;
        if (idx < allCustomTabs.length) {
          onTabChange(allCustomTabs[idx].key);
        }
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onTabChange, allCustomTabs]);

  // -----------------------------------------------------------------------
  // Tab action handlers
  // -----------------------------------------------------------------------
  const handleContextMenu = useCallback(
    (x, y, viewId) => openContextMenu(x, y, viewId),
    [openContextMenu],
  );

  const handleClose = useCallback(
    (viewId) => {
      deleteView(viewId, {
        onSuccess: () => {
          if (activeTab === `view-${viewId}`) {
            onTabChange("traces");
          }
        },
      });
    },
    [deleteView, activeTab, onTabChange],
  );

  const handleRenameSubmit = useCallback(
    (viewId, newName) => {
      updateView({ id: viewId, name: newName });
      stopRenaming();
    },
    [updateView, stopRenaming],
  );

  const handleRenameCancel = useCallback(() => {
    stopRenaming();
  }, [stopRenaming]);

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        minHeight: 36,
        gap: 1,
      }}
    >
      {/* ── Left: Tabs ── */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "4px",
          flex: 1,
          minWidth: 0,
        }}
      >
        {/* Fixed tabs — always pinned */}
        <Box
          data-fixed-section
          sx={{
            display: "flex",
            alignItems: "center",
            gap: "4px",
            flexShrink: 0,
          }}
        >
          {visibleFixedTabs.map((tab) => (
            <FixedTab
              key={tab.key}
              tabKey={tab.key}
              label={tab.label}
              icon={tab.icon}
              shortcut={tab.shortcut}
              isActive={activeTab === tab.key}
              onClick={onTabChange}
            />
          ))}
        </Box>

        {/* Divider between fixed and custom tabs */}
        {customViews.length > 0 && (
          <Divider
            orientation="vertical"
            flexItem
            sx={{ mx: 0.5, my: 1, borderColor: "divider", flexShrink: 0 }}
          />
        )}

        {/* Custom view tabs — horizontally scrollable, sortable via DnD */}
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
            "&::-webkit-scrollbar-thumb:hover": {
              bgcolor: "text.disabled",
            },
          }}
        >
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={sortableIds}
              strategy={horizontalListSortingStrategy}
            >
              {allCustomTabs.map((tab, idx) => (
                <SortableViewTab
                  key={tab.key}
                  tab={tab}
                  idx={idx}
                  isActive={activeTab === tab.key}
                  isDirty={isDirty}
                  isRenaming={editingTabId === tab.view.id}
                  onClick={onTabChange}
                  onClose={handleClose}
                  onContextMenu={handleContextMenu}
                  onRenameSubmit={handleRenameSubmit}
                  onRenameCancel={handleRenameCancel}
                />
              ))}
            </SortableContext>
          </DndContext>
        </Box>

        {/* Create view button — pinned after scrollable area */}
        <CustomTooltip
          show
          title="Create new view"
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
            <Iconify
              icon="mdi:plus"
              width={16}
              sx={{ color: "text.primary" }}
            />
          </ButtonBase>
        </CustomTooltip>
      </Box>

      {/* ── Right: Toolbar portal target + renderRight fallback ── */}
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, flexShrink: 0 }}
      >
        {/* Portal target — ObserveToolbar renders into this */}
        <Box
          id="observe-toolbar-slot"
          sx={{ display: "flex", alignItems: "center", gap: 1 }}
        />
        {renderRight}
      </Box>

      {/* Save View Popover — inline, anchored to "+" button */}
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

ObserveTabBar.propTypes = {
  projectId: PropTypes.string.isRequired,
  activeTab: PropTypes.string.isRequired,
  onTabChange: PropTypes.func.isRequired,
  renderRight: PropTypes.node,
  projectSource: PropTypes.string,
};

export default React.memo(ObserveTabBar);
