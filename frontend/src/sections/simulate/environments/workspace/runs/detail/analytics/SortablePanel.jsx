import { useState } from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from "@mui/material";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

// Wraps one analytics widget so it can be dragged in place and carries its
// actions menu: Edit, Duplicate, Reset to default, width, Download CSV and
// Delete. The widget inside never needs to know it sits in a layout.
export default function SortablePanel({
  id,
  title,
  children,
  sectionColumns,
  currentSpan,
  hasOverrides = false,
  canExport = false,
  onEdit,
  onDuplicate,
  onResetOverride,
  onSetSpan,
  onExport,
  onDelete,
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });
  // The card itself is the drag handle, so Enter or Space on a button inside
  // it (a donut segment, Open task) reaches that button instead of dragging.
  const setRefs = (node) => {
    setNodeRef(node);
    setActivatorNodeRef(node);
  };
  const [menuAnchor, setMenuAnchor] = useState(null);
  const closeMenu = () => setMenuAnchor(null);
  const run = (action) => () => {
    closeMenu();
    action?.();
  };
  // A four-slot section offers a quarter width too.
  const spanOptions =
    sectionColumns >= 4
      ? [
          { label: "Quarter width", value: 1, icon: "solar:widget-2-linear" },
          { label: "Half width", value: 2, icon: "solar:widget-3-linear" },
          { label: "Full width", value: 4, icon: "solar:widget-linear" },
        ]
      : [
          { label: "Half width", value: 1, icon: "solar:widget-2-linear" },
          { label: "Full width", value: 2, icon: "solar:widget-linear" },
        ];

  return (
    <Box
      ref={setRefs}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        zIndex: isDragging ? 4 : "auto",
        // The drag overlay carries the moving copy; the source stays dim.
        opacity: isDragging ? 0.25 : 1,
      }}
      {...attributes}
      {...listeners}
      role="group"
      aria-roledescription="Sortable widget"
      aria-label={`${title} widget`}
      className="sortable-panel-wrap"
      id={`widget-${id}`}
      data-widget-id={id}
      sx={{
        position: "relative",
        height: "100%",
        outline: isDragging ? "2px solid" : "none",
        outlineColor: (theme) => alpha(theme.palette.primary.main, 0.6),
        borderRadius: 1,
        cursor: isDragging ? "grabbing" : "grab",
        touchAction: "none",
        "& > section": { height: "100%" },
        "&:hover .kebab-btn": { opacity: 1 },
      }}
    >
      {hasOverrides && (
        <CustomTooltip
          show
          arrow
          size="small"
          title="Modified. Reset to default from the menu to revert."
        >
          <Box
            aria-label="Modified"
            className="analytics-no-print"
            onPointerDown={(event) => event.stopPropagation()}
            sx={{
              position: "absolute",
              top: 18,
              right: 46,
              zIndex: 3,
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: "warning.main",
              boxShadow: (theme) =>
                `0 0 0 3px ${alpha(theme.palette.warning.main, 0.25)}`,
            }}
          />
        </CustomTooltip>
      )}
      <Box
        className="kebab-btn analytics-no-print"
        onPointerDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
        sx={{
          position: "absolute",
          top: 10,
          right: 10,
          zIndex: 3,
          opacity: menuAnchor ? 1 : 0.7,
          transition: "opacity 120ms",
        }}
      >
        <CustomTooltip show arrow size="small" title="Widget actions">
          <IconButton
            size="small"
            aria-label={`${title} actions`}
            onClick={(event) => {
              event.stopPropagation();
              setMenuAnchor(event.currentTarget);
            }}
            sx={{
              color: "text.subtitle",
              bgcolor: (theme) => alpha(theme.palette.background.default, 0.75),
              "&:hover": {
                color: "text.primary",
                bgcolor: (theme) => alpha(theme.palette.text.primary, 0.12),
              },
            }}
          >
            <Iconify icon="solar:menu-dots-bold" width={16} />
          </IconButton>
        </CustomTooltip>
      </Box>
      <Menu
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={closeMenu}
        // Locking scroll re-lays out every chart on the page on open and close.
        disableScrollLock
        onPointerDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 220 } } }}
      >
        <MenuItem onClick={run(onEdit)}>
          <ListItemIcon>
            <Iconify icon="solar:pen-2-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Edit
          </ListItemText>
        </MenuItem>
        <MenuItem onClick={run(onDuplicate)}>
          <ListItemIcon>
            <Iconify icon="solar:copy-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Duplicate
          </ListItemText>
        </MenuItem>
        <MenuItem disabled={!hasOverrides} onClick={run(onResetOverride)}>
          <ListItemIcon>
            <Iconify icon="solar:restart-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Reset to default
          </ListItemText>
        </MenuItem>
        {spanOptions.map((option) => (
          <MenuItem
            key={option.value}
            selected={currentSpan === option.value}
            onClick={run(() => onSetSpan?.(option.value))}
          >
            <ListItemIcon>
              <Iconify icon={option.icon} width={16} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              {option.label}
            </ListItemText>
            {currentSpan === option.value && (
              <Iconify
                icon="solar:check-circle-bold"
                width={14}
                sx={{ ml: 1, color: "primary.main" }}
              />
            )}
          </MenuItem>
        ))}
        <MenuItem disabled={!canExport} onClick={run(onExport)}>
          <ListItemIcon>
            <Iconify icon="solar:download-minimalistic-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Download CSV
          </ListItemText>
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />
        <MenuItem onClick={run(onDelete)} sx={{ color: "error.main" }}>
          <ListItemIcon sx={{ color: "inherit" }}>
            <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Delete
          </ListItemText>
        </MenuItem>
      </Menu>
      {children}
    </Box>
  );
}
SortablePanel.propTypes = {
  id: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
  children: PropTypes.node,
  sectionColumns: PropTypes.number,
  currentSpan: PropTypes.number,
  hasOverrides: PropTypes.bool,
  canExport: PropTypes.bool,
  onEdit: PropTypes.func,
  onDuplicate: PropTypes.func,
  onResetOverride: PropTypes.func,
  onSetSpan: PropTypes.func,
  onExport: PropTypes.func,
  onDelete: PropTypes.func,
};
