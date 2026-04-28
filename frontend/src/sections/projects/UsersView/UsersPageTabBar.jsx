import React, {
  useCallback,
  useMemo,
  useState,
  useEffect,
  useRef,
} from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Divider } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import FixedTab from "src/components/observe-tabs/FixedTab";
import CustomViewTab from "src/components/observe-tabs/CustomViewTab";
import SaveViewPopover from "src/components/traceDetail/SaveViewDialog";
import {
  useGetWorkspaceSavedViews,
  useCreateWorkspaceSavedView,
  useUpdateWorkspaceSavedView,
  useDeleteWorkspaceSavedView,
} from "src/api/project/saved-views";

const USERS_TAB_TYPE = "users";
const ALL_USERS_KEY = "all";
const ALL_USERS_LABEL = "All Users";

const UsersPageTabBar = ({
  activeTab,
  onTabChange,
  getConfig,
  applyConfig,
}) => {
  const { data: savedViewsData } = useGetWorkspaceSavedViews(USERS_TAB_TYPE);
  const customViews = useMemo(
    () => savedViewsData?.customViews ?? savedViewsData?.custom_views ?? [],
    [savedViewsData],
  );

  const { mutate: createSavedView } =
    useCreateWorkspaceSavedView(USERS_TAB_TYPE);
  const { mutate: updateSavedView } =
    useUpdateWorkspaceSavedView(USERS_TAB_TYPE);
  const { mutate: deleteSavedView } =
    useDeleteWorkspaceSavedView(USERS_TAB_TYPE);

  // Save-view popover state
  const [saveViewAnchor, setSaveViewAnchor] = useState(null);
  const [isSavingView, setIsSavingView] = useState(false);

  // Rename state (scoped locally — no shared tabStore for workspace views)
  const [renamingId, setRenamingId] = useState(null);

  // Track the last tab whose config we successfully applied. Only re-apply on
  // real transitions (not on every customViews refetch), otherwise React Query
  // refetches would stomp in-progress filter/column state.
  const lastAppliedTabRef = useRef(null);

  useEffect(() => {
    if (lastAppliedTabRef.current === activeTab) return;

    if (!activeTab || activeTab === ALL_USERS_KEY) {
      lastAppliedTabRef.current = activeTab;
      applyConfig?.(null);
      return;
    }
    const id = activeTab.startsWith("view-") ? activeTab.slice(5) : null;
    if (!id) {
      lastAppliedTabRef.current = activeTab;
      return;
    }
    const view = customViews.find((v) => v.id === id);
    if (view?.config) {
      applyConfig?.(view.config);
      lastAppliedTabRef.current = activeTab;
    }
    // If not found (customViews not loaded yet), leave lastAppliedTabRef unchanged
    // so the next run after customViews arrives can retry.
  }, [activeTab, applyConfig, customViews]);

  const handleSaveViewConfirm = useCallback(
    (name) => {
      setIsSavingView(true);
      const config = getConfig?.() ?? {};
      createSavedView(
        { name, config },
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
    [createSavedView, getConfig, onTabChange],
  );

  const handleClose = useCallback(
    (viewId) => {
      deleteSavedView(viewId, {
        onSuccess: () => {
          if (activeTab === `view-${viewId}`) onTabChange?.(ALL_USERS_KEY);
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
        width: "100%",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: "4px",
          flex: 1,
          minWidth: 0,
        }}
      >
        {/* Fixed "All Users" tab */}
        <FixedTab
          tabKey={ALL_USERS_KEY}
          label={ALL_USERS_LABEL}
          icon="mdi:account-multiple-outline"
          shortcut="1"
          isActive={activeTab === ALL_USERS_KEY}
          onClick={onTabChange}
        />

        {customViews.length > 0 && (
          <Divider
            orientation="vertical"
            flexItem
            sx={{ mx: 0.5, my: 1, borderColor: "divider", flexShrink: 0 }}
          />
        )}

        {/* Custom view tabs */}
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
              shortcut={idx + 2 <= 9 ? String(idx + 2) : undefined}
              isActive={activeTab === `view-${view.id}`}
              isRenaming={renamingId === view.id}
              onClick={onTabChange}
              onClose={handleClose}
              onContextMenu={(x, y, id) => setRenamingId(id)}
              onRenameSubmit={handleRenameSubmit}
              onRenameCancel={() => setRenamingId(null)}
            />
          ))}
        </Box>

        {/* Create view button */}
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
            <Iconify
              icon="mdi:plus"
              width={16}
              sx={{ color: "text.primary" }}
            />
          </ButtonBase>
        </CustomTooltip>
      </Box>

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

UsersPageTabBar.propTypes = {
  activeTab: PropTypes.string.isRequired,
  onTabChange: PropTypes.func.isRequired,
  getConfig: PropTypes.func.isRequired,
  applyConfig: PropTypes.func.isRequired,
};

export default UsersPageTabBar;
