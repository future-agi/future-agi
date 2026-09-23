import React, { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
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
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import FixedTab from "src/components/observe-tabs/FixedTab";
import CustomViewTab from "src/components/observe-tabs/CustomViewTab";
import SaveViewPopover from "src/components/traceDetail/SaveViewDialog";

/*
  Analytics views as tabs — the Observe tab bar flow (fixed tab, saved-view
  tabs, Save view popover, drag reorder, 1-9 shortcuts, right-click menu)
  over the local run-layout store instead of the saved-views API.
  A view is keyed by its name, so the name doubles as the tab id.
*/

const SortableViewTab = ({ name, idx, ...tabProps }) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: name });
  return (
    <Box
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
        flexShrink: 0,
        cursor: "grab",
      }}
      {...attributes}
      {...listeners}
    >
      <CustomViewTab
        view={{ id: name, name }}
        shortcut={idx + 2 <= 9 ? String(idx + 2) : undefined}
        {...tabProps}
      />
    </Box>
  );
};

SortableViewTab.propTypes = {
  name: PropTypes.string.isRequired,
  idx: PropTypes.number.isRequired,
};

/* Same menu as Observe's TabContextMenu, minus "Share with team" —
   analytics views live in this browser only. */
const ViewContextMenu = ({ anchor, onClose, onRename, onDuplicate, onDelete }) => {
  const [confirmOpen, setConfirmOpen] = useState(false);
  if (!anchor) return null;
  return (
    <>
      <Menu
        open={!confirmOpen}
        onClose={onClose}
        anchorReference="anchorPosition"
        anchorPosition={{ top: anchor.y, left: anchor.x }}
        slotProps={{ paper: { sx: { minWidth: 180 } } }}
      >
        <MenuItem dense onClick={() => { onClose(); onRename(anchor.name); }}>
          <ListItemIcon><Iconify icon="mdi:pencil-outline" width={18} /></ListItemIcon>
          <ListItemText primaryTypographyProps={{ variant: "body2" }}>Rename</ListItemText>
        </MenuItem>
        <MenuItem dense onClick={() => { onClose(); onDuplicate(anchor.name); }}>
          <ListItemIcon><Iconify icon="mdi:content-copy" width={18} /></ListItemIcon>
          <ListItemText primaryTypographyProps={{ variant: "body2" }}>Duplicate</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem dense onClick={() => setConfirmOpen(true)} sx={{ color: "error.main" }}>
          <ListItemIcon sx={{ color: "inherit" }}><Iconify icon="mdi:delete-outline" width={18} /></ListItemIcon>
          <ListItemText primaryTypographyProps={{ variant: "body2" }}>Delete</ListItemText>
        </MenuItem>
      </Menu>

      <Dialog open={confirmOpen} onClose={() => { setConfirmOpen(false); onClose(); }} maxWidth="xs">
        <DialogTitle>Delete &ldquo;{anchor.name}&rdquo;?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This view will be permanently removed. This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button size="small" onClick={() => { setConfirmOpen(false); onClose(); }}>Cancel</Button>
          <Button
            size="small" color="error" variant="contained"
            onClick={() => { onDelete(anchor.name); setConfirmOpen(false); onClose(); }}
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

ViewContextMenu.propTypes = {
  anchor: PropTypes.shape({ x: PropTypes.number, y: PropTypes.number, name: PropTypes.string }),
  onClose: PropTypes.func.isRequired,
  onRename: PropTypes.func.isRequired,
  onDuplicate: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
};

export default function ViewTabBar({
  activeView,
  viewNames,
  defaultName,
  onSwitch,
  onSave,
  onRename,
  onDuplicate,
  onDelete,
  onReorder,
}) {
  const customNames = viewNames.filter((n) => n !== defaultName);
  const [saveAnchor, setSaveAnchor] = useState(null);
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [renaming, setRenaming] = useState(null);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const handleSave = useCallback((name) => {
    onSave(name);
    enqueueSnackbar("View created", { variant: "success" });
    setSaveAnchor(null);
  }, [onSave]);

  const handleDragEnd = useCallback(({ active, over }) => {
    if (!over || active.id === over.id) return;
    const from = customNames.indexOf(active.id);
    const to = customNames.indexOf(over.id);
    if (from === -1 || to === -1) return;
    const next = [...customNames];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onReorder(next);
  }, [customNames, onReorder]);

  /* 1 = Default, 2-9 = saved views — same scheme as Observe's tabs. */
  useEffect(() => {
    const handleKeyDown = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (document.activeElement?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const num = parseInt(e.key, 10);
      if (Number.isNaN(num) || num < 1 || num > 9) return;
      if (num === 1) onSwitch(defaultName);
      else if (customNames[num - 2]) onSwitch(customNames[num - 2]);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onSwitch, defaultName, customNames]);

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: "4px", flex: 1, minWidth: 0, minHeight: 36 }}>
      <Box sx={{ flexShrink: 0 }}>
        <FixedTab
          tabKey={defaultName}
          label={defaultName}
          icon="mdi:view-dashboard-outline"
          shortcut="1"
          isActive={activeView === defaultName}
          onClick={onSwitch}
        />
      </Box>

      {customNames.length > 0 && (
        <Divider orientation="vertical" flexItem sx={{ mx: 0.5, my: 1, borderColor: "divider", flexShrink: 0 }} />
      )}

      <Box
        sx={{
          display: "flex", alignItems: "center", gap: "4px", minWidth: 0,
          overflowX: "auto", overflowY: "hidden", scrollbarWidth: "thin",
          "&::-webkit-scrollbar": { height: 6 },
          "&::-webkit-scrollbar-track": { bgcolor: "transparent" },
          "&::-webkit-scrollbar-thumb": { bgcolor: "divider", borderRadius: 3 },
          "&::-webkit-scrollbar-thumb:hover": { bgcolor: "text.disabled" },
        }}
      >
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={customNames} strategy={horizontalListSortingStrategy}>
            {customNames.map((name, idx) => (
              <SortableViewTab
                key={name}
                name={name}
                idx={idx}
                isActive={activeView === name}
                isRenaming={renaming === name}
                onClick={(tabKey) => onSwitch(tabKey.replace(/^view-/, ""))}
                onClose={onDelete}
                onContextMenu={(x, y, id) => setMenuAnchor({ x, y, name: id })}
                onRenameSubmit={(from, to) => { onRename(from, to); setRenaming(null); }}
                onRenameCancel={() => setRenaming(null)}
              />
            ))}
          </SortableContext>
        </DndContext>
      </Box>

      <ButtonBase
        onClick={(e) => setSaveAnchor(e.currentTarget)}
        sx={{
          display: "inline-flex", alignItems: "center", gap: 0.5,
          height: 26, px: "8px", flexShrink: 0,
          border: "1px solid", borderColor: "divider", borderRadius: "4px",
          bgcolor: "background.paper",
          "&:hover": { bgcolor: "background.neutral" },
        }}
      >
        <Iconify icon="mdi:plus" width={16} sx={{ color: "text.primary" }} />
        <Typography
          sx={{
            fontSize: 13, fontWeight: 500, lineHeight: "20px", whiteSpace: "nowrap",
            fontFamily: "'IBM Plex Sans', sans-serif", color: "text.primary",
          }}
        >
          Save view
        </Typography>
      </ButtonBase>

      <SaveViewPopover
        anchorEl={saveAnchor}
        open={Boolean(saveAnchor)}
        onClose={() => setSaveAnchor(null)}
        onSave={handleSave}
      />

      <ViewContextMenu
        anchor={menuAnchor}
        onClose={() => setMenuAnchor(null)}
        onRename={setRenaming}
        onDuplicate={onDuplicate}
        onDelete={onDelete}
      />
    </Box>
  );
}

ViewTabBar.propTypes = {
  activeView: PropTypes.string.isRequired,
  viewNames: PropTypes.arrayOf(PropTypes.string).isRequired,
  defaultName: PropTypes.string.isRequired,
  onSwitch: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  onRename: PropTypes.func.isRequired,
  onDuplicate: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
  onReorder: PropTypes.func.isRequired,
};
