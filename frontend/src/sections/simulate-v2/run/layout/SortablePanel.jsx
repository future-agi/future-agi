import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Tooltip, IconButton, Menu, MenuItem, ListItemIcon, ListItemText, Divider,
} from "@mui/material";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";

/**
 * Wraps every rendered analytics panel so it can be dragged in place
 * and gets its own kebab-menu of actions. Sits between the layout
 * renderer and the actual panel component, so no panel needs to
 * know it's inside a customisable layout.
 *
 * Layout of overlays:
 *   ┌ panel ────────────────────────────────────┐
 *   │  ⋮⋮        (title)         info    ⋮ [menu] │  ← header
 *   │                                            │
 *   │  … the panel's own body …                  │
 *   └────────────────────────────────────────────┘
 *
 * The drag handle (⋮⋮) sits over the panel's top-left and only
 * shows on hover so it doesn't compete with the title. The kebab
 * (⋮) sits over the top-right, always visible, and opens a menu
 * with reorder / resize / duplicate / hide / delete / export.
 */
export default function SortablePanel({
  id, children,
  sectionColumns, currentSpan,
  onSetSpan,
  onHide, onDuplicate, onDelete, onExport,
  onEdit, onRename, onCustomizeCopy,
  onResetOverride, hasOverrides,
  isCustom, hasExport,
  /* Retained on the props signature so the shell doesn't need to
     rewire — the menu just doesn't surface these any more. */
  // eslint-disable-next-line no-unused-vars
  isFirstInSection, isLastInSection,
  // eslint-disable-next-line no-unused-vars
  onMoveUp, onMoveDown, onMoveTop, onMoveBottom, onResetSpan,
  // eslint-disable-next-line no-unused-vars
  onCopyLink, onPrintOnly, hasEdit,
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 4 : "auto",
    /* Dim the source card while it's being dragged — the DragOverlay
       ghost in the shell carries the "in-flight" copy, so keeping
       the source visible at full opacity looks like two of the same
       widget on screen. */
    opacity: isDragging ? 0.25 : 1,
  };
  const [menuAnchor, setMenuAnchor] = useState(null);
  const openMenu = (e) => { e.stopPropagation(); setMenuAnchor(e.currentTarget); };
  const closeMenu = () => setMenuAnchor(null);
  const call = (fn) => () => { closeMenu(); fn?.(); };
  // eslint-disable-next-line no-unused-vars
  const { enqueueSnackbar } = useSnackbar();

  /* Resize options depend on the section's column count. In a
     2-column section the choice is Half or Full; in a 4-column
     Breakdowns section it's Quarter / Half / Full. */
  const spanOptions = sectionColumns >= 4
    ? [
      { label: "Quarter width", value: 1, icon: "solar:widget-2-linear" },
      { label: "Half width",    value: 2, icon: "solar:widget-3-linear" },
      { label: "Full width",    value: 4, icon: "solar:widget-linear" },
    ]
    : [
      { label: "Half width",    value: 1, icon: "solar:widget-2-linear" },
      { label: "Full width",    value: 2, icon: "solar:widget-linear" },
    ];

  return (
    <Box
      ref={setNodeRef} style={style}
      {...attributes} {...listeners}
      className="sortable-panel-wrap"
      id={`widget-${id}`}
      data-widget-id={id}
      sx={{
        position: "relative", height: "100%",
        outline: isDragging ? "2px solid" : "none",
        outlineColor: (t) => alpha(t.palette.primary.main, 0.6),
        borderRadius: 2,
        transition: "outline-color 120ms",
        /* Whole card is a drag source. dnd-kit's PointerSensor with
           distance:6 activation means a stationary click still fires
           the underlying handler (donut slice, tooltip, etc.) — only
           a real drag motion starts the reorder. cursor: grab hints
           the affordance; changes to grabbing while dragging. */
        cursor: isDragging ? "grabbing" : "grab",
        touchAction: "none",
        "&:hover .kebab-btn": { opacity: 1 },
      }}
    >
      {/* Modified indicator — a small amber dot when the widget
          has any config overrides. Sits to the left of the kebab
          so the "there's an override here" cue is visible without
          opening the menu. */}
      {hasOverrides && (
        <Tooltip arrow title="Modified — Reset to default from the menu to revert.">
          <Box
            className="modified-dot analytics-no-print"
            onPointerDown={(e) => e.stopPropagation()}
            sx={{
              position: "absolute", top: 12, right: 42, zIndex: 3,
              width: 8, height: 8, borderRadius: "50%",
              bgcolor: "#F59E0B",
              boxShadow: (t) => `0 0 0 3px ${alpha("#F59E0B", t.palette.mode === "dark" ? 0.2 : 0.15)}`,
            }}
          />
        </Tooltip>
      )}

      {/* Kebab — top-right, always visible. onPointerDown stops
          drag activation so the click opens the menu instead of
          starting a drag. */}
      <Box
        className="kebab-btn analytics-no-print"
        onPointerDown={(e) => e.stopPropagation()}
        sx={{
          position: "absolute", top: 8, right: 8, zIndex: 3,
          opacity: menuAnchor ? 1 : 0.7,
          transition: "opacity 120ms",
        }}
      >
        <Tooltip arrow title="Widget actions">
          <IconButton
            size="small" onClick={openMenu} aria-label="Widget actions"
            sx={{
              width: 26, height: 26, borderRadius: 1,
              bgcolor: (t) => alpha(t.palette.background.default, 0.75),
              color: "text.subtitle",
              "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, 0.12), color: "text.primary" },
            }}
          >
            <Iconify icon="solar:menu-dots-bold" width={16} />
          </IconButton>
        </Tooltip>
      </Box>

      <Menu
        anchorEl={menuAnchor}
        open={!!menuAnchor}
        onClose={closeMenu}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 220 } } }}
      >
        {/* Edit — custom widgets only. Built-ins stay curated;
            users reshape them via "Customize a copy" below. */}
        {isCustom && (
          <MenuItem onClick={call(onEdit)}>
            <ListItemIcon><Iconify icon="solar:pen-2-linear" width={16} /></ListItemIcon>
            <ListItemText>Edit</ListItemText>
          </MenuItem>
        )}

        {/* Rename — custom widgets only, for the same reason. */}
        {isCustom && (
          <MenuItem onClick={call(onRename)}>
            <ListItemIcon><Iconify icon="solar:tag-linear" width={16} /></ListItemIcon>
            <ListItemText>Rename</ListItemText>
          </MenuItem>
        )}

        {/* Duplicate — copies a custom widget's config as a new
            widget right after it. */}
        {isCustom && (
          <MenuItem onClick={call(onDuplicate)}>
            <ListItemIcon><Iconify icon="solar:copy-linear" width={16} /></ListItemIcon>
            <ListItemText>Duplicate</ListItemText>
          </MenuItem>
        )}

        {/* Customize a copy — the built-in stays curated; the user
            gets a fully editable snapshot of it in the custom-widget
            editor to reshape however they want. */}
        {!isCustom && (
          <MenuItem onClick={call(onCustomizeCopy)}>
            <ListItemIcon><Iconify icon="solar:widget-add-linear" width={16} /></ListItemIcon>
            <ListItemText>Customize a copy</ListItemText>
          </MenuItem>
        )}

        {/* Reset to default — only when the widget has overrides. For
            built-ins this restores the shipped defaults; for custom
            widgets it reverts to the config as-of the last save. */}
        {hasOverrides && (
          <MenuItem onClick={call(onResetOverride)}>
            <ListItemIcon><Iconify icon="solar:restart-linear" width={16} /></ListItemIcon>
            <ListItemText>Reset to default</ListItemText>
          </MenuItem>
        )}

        {/* Resize width — section-aware options (Quarter/Half/Full in a
            4-column section, Half/Full otherwise). */}
        {spanOptions.map((opt) => (
          <MenuItem
            key={opt.value}
            selected={currentSpan === opt.value}
            onClick={call(() => onSetSpan?.(opt.value))}
          >
            <ListItemIcon><Iconify icon={opt.icon} width={16} /></ListItemIcon>
            <ListItemText>{opt.label}</ListItemText>
            {currentSpan === opt.value && (
              <Iconify icon="solar:check-circle-bold" width={14} sx={{ ml: 1, color: "primary.main" }} />
            )}
          </MenuItem>
        ))}

        {/* Download — only when the widget exposes an export set. */}
        {hasExport && (
          <MenuItem onClick={call(onExport)}>
            <ListItemIcon><Iconify icon="solar:download-minimalistic-linear" width={16} /></ListItemIcon>
            <ListItemText>Download</ListItemText>
          </MenuItem>
        )}

        <Divider sx={{ my: 0.5 }} />

        {/* Delete — for a built-in the widget goes to the Hidden
            bucket (reversible from the Customize drawer). A custom
            widget's config is removed permanently. */}
        <MenuItem
          onClick={call(isCustom ? onDelete : onHide)}
          sx={{ color: "#DC2626" }}
        >
          <ListItemIcon><Iconify icon="solar:trash-bin-trash-linear" width={16} sx={{ color: "#DC2626" }} /></ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>

      {/* Panel body */}
      {children}
    </Box>
  );
}
SortablePanel.propTypes = {
  id: PropTypes.string.isRequired, children: PropTypes.node.isRequired,
  sectionColumns: PropTypes.number, currentSpan: PropTypes.number,
  isFirstInSection: PropTypes.bool, isLastInSection: PropTypes.bool,
  onMoveUp: PropTypes.func, onMoveDown: PropTypes.func, onMoveTop: PropTypes.func, onMoveBottom: PropTypes.func,
  onSetSpan: PropTypes.func, onResetSpan: PropTypes.func,
  onHide: PropTypes.func, onDuplicate: PropTypes.func, onEdit: PropTypes.func, onDelete: PropTypes.func,
  onRename: PropTypes.func, onCustomizeCopy: PropTypes.func,
  onExport: PropTypes.func, onCopyLink: PropTypes.func, onPrintOnly: PropTypes.func,
  isCustom: PropTypes.bool, hasEdit: PropTypes.bool, hasExport: PropTypes.bool,
};
