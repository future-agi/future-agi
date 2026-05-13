import React, { forwardRef, useRef } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Divider, Stack, Typography } from "@mui/material";
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

// Shared 24px bordered pill button
const ToolbarPill = forwardRef(({ icon, label, onClick, sx }, ref) => (
  <ButtonBase
    ref={ref}
    onClick={onClick}
    sx={{
      display: "inline-flex",
      alignItems: "center",
      gap: 0.5,
      height: 24,
      px: 1,
      border: "1px solid",
      borderColor: "divider",
      borderRadius: "4px",
      bgcolor: "transparent",
      fontSize: 11,
      fontWeight: 400,
      fontFamily: "'Inter', sans-serif",
      color: "text.primary",
      whiteSpace: "nowrap",
      flexShrink: 0,
      "&:hover": {
        bgcolor: "action.hover",
        borderColor: "text.disabled",
      },
      ...sx,
    }}
  >
    {icon && <Iconify icon={icon} width={14} />}
    {label && <span>{label}</span>}
  </ButtonBase>
));

ToolbarPill.displayName = "ToolbarPill";

ToolbarPill.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  onClick: PropTypes.func,
  sx: PropTypes.object,
};

// Tab pill inside the toolbar
const TabPill = ({
  label,
  icon,
  isActive,
  isDefault,
  onClick,
  onClose,
  tooltip,
}) => {
  const pill = (
    <Box
      onClick={onClick}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        px: 1,
        py: 0.5,
        cursor: "pointer",
        borderBottom: isActive ? "2px solid" : "2px solid transparent",
        borderColor: isActive ? "primary.main" : "transparent",
        flexShrink: 0,
        "&:hover": { bgcolor: "action.hover", borderRadius: "4px 4px 0 0" },
      }}
    >
      {icon && (
        <Iconify
          icon={icon}
          width={14}
          color={isActive ? "primary.main" : "text.secondary"}
        />
      )}
      <Typography
        variant="body2"
        sx={{
          fontSize: 12,
          fontWeight: isActive ? 600 : 400,
          color: isActive ? "primary.main" : "text.secondary",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </Typography>
      {!isDefault && onClose && (
        <Iconify
          icon="mdi:close"
          width={12}
          color="text.disabled"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          sx={{
            ml: 0.25,
            cursor: "pointer",
            borderRadius: "50%",
            "&:hover": { bgcolor: "action.hover" },
          }}
        />
      )}
    </Box>
  );

  if (!tooltip) return pill;
  return (
    <CustomTooltip
      show
      title={tooltip}
      placement="bottom"
      arrow
      size="small"
      type="black"
    >
      {pill}
    </CustomTooltip>
  );
};

TabPill.propTypes = {
  label: PropTypes.string.isRequired,
  icon: PropTypes.string,
  isActive: PropTypes.bool,
  isDefault: PropTypes.bool,
  onClick: PropTypes.func,
  onClose: PropTypes.func,
  tooltip: PropTypes.string,
};

// Sortable wrapper — draggable via dnd-kit for non-default tabs
const SortableTabPill = ({ tab, ...pillProps }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: tab.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    display: "inline-flex",
    flexShrink: 0,
    cursor: "grab",
  };

  return (
    <Box ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <TabPill {...pillProps} />
    </Box>
  );
};

SortableTabPill.propTypes = {
  tab: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
};

// ---------------------------------------------------------------------------
// DrawerToolbar
// ---------------------------------------------------------------------------
const DrawerToolbar = ({
  tabs,
  activeTabId,
  onTabChange,
  onCloseTab,
  onCreateTab,
  onCreateImagineTab,
  onReorderTabs,
  onFilterOpen,
  onDisplayOpen,
  hasActiveFilter,
  readOnly = false,
  readOnlyTabTooltip,
  hideFilter = false,
  hideDisplay = false,
  rightSlot = null,
}) => {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );
  const displayRef = useRef(null);

  const effectiveTabs = tabs?.length
    ? tabs
    : [
        {
          id: "trace",
          label: "Trace",
          icon: "mdi:link-variant",
          isDefault: true,
        },
      ];

  const defaultTabs = effectiveTabs.filter((t) => t.isDefault);
  const customTabs = effectiveTabs.filter((t) => !t.isDefault);
  const sortableIds = customTabs.map((t) => t.id);

  const handleDragEnd = (event) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = customTabs.findIndex((t) => t.id === active.id);
    const newIndex = customTabs.findIndex((t) => t.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;
    const reordered = [...customTabs];
    const [moved] = reordered.splice(oldIndex, 1);
    reordered.splice(newIndex, 0, moved);
    onReorderTabs?.(reordered.map((t) => t.id));
  };

  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      sx={{
        px: 1.5,
        py: 0,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        minHeight: 36,
      }}
    >
      {/* Left: Tabs */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0,
          flex: 1,
          minWidth: 0,
        }}
      >
        {/* Default tabs — pinned, not draggable */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flexShrink: 0,
          }}
        >
          {defaultTabs.map((tab) => (
            <TabPill
              key={tab.id}
              label={tab.label}
              icon={tab.icon}
              isActive={(activeTabId || "trace") === tab.id}
              isDefault={tab.isDefault}
              onClick={() => onTabChange?.(tab.id)}
              tooltip={readOnly ? readOnlyTabTooltip : undefined}
            />
          ))}
        </Box>

        {/* Custom tabs — horizontally scrollable, draggable to reorder */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
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
          {onReorderTabs ? (
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={sortableIds}
                strategy={horizontalListSortingStrategy}
              >
                {customTabs.map((tab) => (
                  <SortableTabPill
                    key={tab.id}
                    tab={tab}
                    label={tab.label}
                    icon={tab.icon}
                    isActive={(activeTabId || "trace") === tab.id}
                    isDefault={false}
                    onClick={() => onTabChange?.(tab.id)}
                    onClose={readOnly ? null : () => onCloseTab?.(tab.id)}
                    tooltip={readOnly ? readOnlyTabTooltip : undefined}
                  />
                ))}
              </SortableContext>
            </DndContext>
          ) : (
            customTabs.map((tab) => (
              <TabPill
                key={tab.id}
                label={tab.label}
                icon={tab.icon}
                isActive={(activeTabId || "trace") === tab.id}
                isDefault={false}
                onClick={() => onTabChange?.(tab.id)}
                onClose={readOnly ? null : () => onCloseTab?.(tab.id)}
                tooltip={readOnly ? readOnlyTabTooltip : undefined}
              />
            ))
          )}
        </Box>

        {/* "+" create view button — hidden in read-only mode */}
        {!readOnly && (
          <CustomTooltip
            show
            title="Create new view"
            placement="bottom"
            arrow
            size="small"
            type="black"
          >
            <ButtonBase
              onClick={(e) => onCreateTab?.(e)}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                height: 20,
                width: 20,
                borderRadius: "4px",
                ml: 0.5,
                flexShrink: 0,
                color: "text.disabled",
                "&:hover": { bgcolor: "action.hover", color: "text.secondary" },
              }}
            >
              <Iconify icon="mdi:plus" width={14} />
            </ButtonBase>
          </CustomTooltip>
        )}

        {/* Imagine with Falcon — only in trace detail, not list page or read-only */}
        {!readOnly && onCreateImagineTab && (
          <CustomTooltip
            show
            title="Imagine with Falcon"
            placement="bottom"
            arrow
            size="small"
            type="black"
          >
            <ButtonBase
              onClick={() => onCreateImagineTab?.()}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.5,
                height: 22,
                px: 0.75,
                borderRadius: "4px",
                ml: 0.5,
                flexShrink: 0,
                color: "text.disabled",
                "&:hover": {
                  background:
                    "linear-gradient(135deg, rgba(123,86,219,0.08) 0%, rgba(26,188,254,0.08) 100%)",
                  color: "#7B56DB",
                },
              }}
            >
              <Iconify icon="mdi:creation" width={14} />
              <Typography sx={{ fontSize: 11, fontWeight: 500, lineHeight: 1 }}>
                Imagine
              </Typography>
            </ButtonBase>
          </CustomTooltip>
        )}
      </Box>

      {/* Right: Action buttons — hidden when Imagine tab is active */}
      {(activeTabId || "trace") !== "__new_imagine__" &&
        !(tabs || []).find(
          (t) => t.id === activeTabId && t.tabType === "imagine",
        ) && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{ flexShrink: 0 }}
          >
            {(!hideFilter || !hideDisplay || rightSlot) && (
              <Divider
                orientation="vertical"
                flexItem
                sx={{ my: 0.75, borderColor: "divider" }}
              />
            )}

            {/* Filter — red dot when active */}
            {!hideFilter && (
              <Box sx={{ position: "relative", display: "inline-flex" }}>
                <ToolbarPill
                  icon="mdi:filter-outline"
                  onClick={(e) => onFilterOpen?.(e)}
                />
                {hasActiveFilter && (
                  <Box
                    sx={{
                      position: "absolute",
                      top: 2,
                      right: 2,
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      bgcolor: "#ef4444",
                      border: "1px solid",
                      borderColor: "background.paper",
                    }}
                  />
                )}
              </Box>
            )}

            {/* Display */}
            {!hideDisplay && (
              <ToolbarPill
                ref={displayRef}
                icon="mdi:tune-vertical"
                label="Display"
                onClick={(e) => onDisplayOpen?.(e.currentTarget)}
              />
            )}

            {rightSlot}
          </Stack>
        )}
    </Stack>
  );
};

DrawerToolbar.propTypes = {
  tabs: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      icon: PropTypes.string,
      isDefault: PropTypes.bool,
    }),
  ),
  activeTabId: PropTypes.string,
  onTabChange: PropTypes.func,
  onCloseTab: PropTypes.func,
  onCreateTab: PropTypes.func,
  onCreateImagineTab: PropTypes.func,
  onReorderTabs: PropTypes.func,
  onFilterOpen: PropTypes.func,
  onDisplayOpen: PropTypes.func,
  hasActiveFilter: PropTypes.bool,
  readOnly: PropTypes.bool,
  readOnlyTabTooltip: PropTypes.string,
  hideFilter: PropTypes.bool,
  hideDisplay: PropTypes.bool,
  rightSlot: PropTypes.node,
};

export default React.memo(DrawerToolbar);
