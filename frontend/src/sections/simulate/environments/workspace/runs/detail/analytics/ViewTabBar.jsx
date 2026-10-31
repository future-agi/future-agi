import { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import {
  DndContext,
  PointerSensor,
  closestCenter,
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
import CustomTooltip from "src/components/tooltip";
import { ConfirmDialog } from "src/components/custom-dialog";
import FixedTab from "src/components/observe-tabs/FixedTab";
import CustomViewTab from "src/components/observe-tabs/CustomViewTab";
import SaveViewPopover from "src/components/traceDetail/SaveViewDialog";

// Analytics views as tabs: the Observe tab bar flow (fixed Default tab, saved
// view tabs, Save view, drag reorder, 1-9 shortcuts, right-click menu) over
// the in-memory layout. A view is keyed by its name. Without `onSave`, Save
// view is shown disabled: views can't be saved yet.
function SortableViewTab({ name, index, ...tabProps }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: name });
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
        shortcut={index + 2 <= 9 ? String(index + 2) : undefined}
        {...tabProps}
      />
    </Box>
  );
}
SortableViewTab.propTypes = {
  name: PropTypes.string.isRequired,
  index: PropTypes.number.isRequired,
};

function ViewContextMenu({ anchor, onClose, onRename, onDuplicate, onDelete }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  if (!anchor) return null;
  const close = () => {
    setConfirmOpen(false);
    onClose();
  };
  return (
    <>
      <Menu
        open={!confirmOpen}
        onClose={onClose}
        anchorReference="anchorPosition"
        anchorPosition={{ top: anchor.y, left: anchor.x }}
        slotProps={{ paper: { sx: { minWidth: 200 } } }}
      >
        <MenuItem
          dense
          onClick={() => {
            onClose();
            onRename(anchor.name);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:pen-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Rename
          </ListItemText>
        </MenuItem>
        <MenuItem
          dense
          onClick={() => {
            onClose();
            onDuplicate(anchor.name);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:copy-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Duplicate
          </ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem
          dense
          onClick={() => setConfirmOpen(true)}
          sx={{ color: "error.main" }}
        >
          <ListItemIcon sx={{ color: "inherit" }}>
            <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Delete
          </ListItemText>
        </MenuItem>
      </Menu>
      <ConfirmDialog
        open={confirmOpen}
        onClose={close}
        title={`Delete "${anchor.name}"?`}
        content="This view will be permanently removed. This action cannot be undone."
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              onDelete(anchor.name);
              close();
            }}
          >
            Delete
          </Button>
        }
      />
    </>
  );
}
ViewContextMenu.propTypes = {
  anchor: PropTypes.shape({
    x: PropTypes.number,
    y: PropTypes.number,
    name: PropTypes.string,
  }),
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
  adornment = null,
  onRename,
  onDuplicate,
  onDelete,
  onReorder,
}) {
  const customNames = viewNames.filter((name) => name !== defaultName);
  const [saveAnchor, setSaveAnchor] = useState(null);
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const handleSave = useCallback(
    (name) => {
      onSave(name);
      enqueueSnackbar("View created", { variant: "success" });
      setSaveAnchor(null);
    },
    [onSave],
  );

  const handleDragEnd = ({ active, over }) => {
    if (!over || active.id === over.id) return;
    const from = customNames.indexOf(active.id);
    const to = customNames.indexOf(over.id);
    if (from === -1 || to === -1) return;
    const next = [...customNames];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onReorder(next);
  };

  // 1 is Default, 2-9 the saved views, as on Observe's tabs.
  useEffect(() => {
    const handleKeyDown = (event) => {
      const active = document.activeElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(active?.tagName)) return;
      if (active?.isContentEditable) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const number = parseInt(event.key, 10);
      if (Number.isNaN(number) || number < 1 || number > 9) return;
      if (number === 1) onSwitch(defaultName);
      else if (customNames[number - 2]) onSwitch(customNames[number - 2]);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onSwitch, defaultName, customNames]);

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        flex: 1,
        minWidth: 0,
        minHeight: 36,
      }}
    >
      <Box sx={{ flexShrink: 0 }}>
        <FixedTab
          tabKey={defaultName}
          label={defaultName}
          icon="solar:widget-4-linear"
          shortcut="1"
          isActive={activeView === defaultName}
          onClick={onSwitch}
        />
      </Box>
      {customNames.length > 0 && (
        <Divider
          orientation="vertical"
          flexItem
          sx={{ mx: 0.5, my: 1, flexShrink: 0 }}
        />
      )}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          minWidth: 0,
          overflowX: "auto",
          overflowY: "hidden",
          scrollbarWidth: "thin",
        }}
      >
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={customNames}
            strategy={horizontalListSortingStrategy}
          >
            {customNames.map((name, index) => (
              <SortableViewTab
                key={name}
                name={name}
                index={index}
                isActive={activeView === name}
                isRenaming={renaming === name}
                onClick={(tabKey) => onSwitch(tabKey.replace(/^view-/, ""))}
                onClose={onDelete}
                onContextMenu={(x, y, id) => setMenuAnchor({ x, y, name: id })}
                onRenameSubmit={(from, to) => {
                  onRename(from, to);
                  setRenaming(null);
                }}
                onRenameCancel={() => setRenaming(null)}
              />
            ))}
          </SortableContext>
        </DndContext>
      </Box>
      <CustomTooltip
        show={!onSave}
        arrow
        size="small"
        title="Saving views isn't available yet."
      >
        <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
          <ButtonBase
            disabled={!onSave}
            onClick={(event) => setSaveAnchor(event.currentTarget)}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              height: 26,
              px: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              bgcolor: "background.paper",
              "&:hover": { bgcolor: "background.neutral" },
              "&.Mui-disabled": { color: "text.disabled" },
            }}
          >
            <Iconify icon="solar:add-linear" width={16} />
            <Typography
              typography="s1"
              sx={{
                fontWeight: 500,
                whiteSpace: "nowrap",
                color: onSave ? "text.primary" : "inherit",
              }}
            >
              Save view
            </Typography>
          </ButtonBase>
        </Box>
      </CustomTooltip>
      {adornment}
      {onSave && (
        <SaveViewPopover
          anchorEl={saveAnchor}
          open={Boolean(saveAnchor)}
          onClose={() => setSaveAnchor(null)}
          onSave={handleSave}
          existingNames={viewNames}
        />
      )}
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
  // Shown right after Save view, e.g. a UI only flag.
  adornment: PropTypes.node,
  activeView: PropTypes.string.isRequired,
  viewNames: PropTypes.arrayOf(PropTypes.string).isRequired,
  defaultName: PropTypes.string.isRequired,
  onSwitch: PropTypes.func.isRequired,
  // Leave out while views can't be saved: Save view shows disabled.
  onSave: PropTypes.func,
  onRename: PropTypes.func.isRequired,
  onDuplicate: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
  onReorder: PropTypes.func.isRequired,
};
