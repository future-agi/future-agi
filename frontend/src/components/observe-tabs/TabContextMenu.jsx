import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useUpdateSavedView,
  useDeleteSavedView,
  useDuplicateSavedView,
} from "src/api/project/saved-views";

const TabContextMenu = ({
  anchorPosition,
  view,
  projectId,
  onClose,
  onRename,
  onTabChange,
}) => {
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const { mutate: updateView } = useUpdateSavedView(projectId);
  const { mutate: deleteView } = useDeleteSavedView(projectId);
  const { mutate: duplicateView } = useDuplicateSavedView(projectId);

  if (!view || !anchorPosition) return null;

  const isShared = view.visibility === "project";

  const handleRename = () => {
    onClose();
    onRename(view.id);
  };

  const handleDuplicate = () => {
    duplicateView(
      { id: view.id },
      {
        onSuccess: (res) => {
          const newView = res.data?.result;
          if (newView?.id) {
            onTabChange(`view-${newView.id}`);
          }
        },
      },
    );
    onClose();
  };

  const handleToggleVisibility = () => {
    updateView({
      id: view.id,
      visibility: isShared ? "personal" : "project",
    });
    onClose();
  };

  const handleDeleteConfirm = () => {
    deleteView(view.id, {
      onSuccess: () => {
        onTabChange("traces");
      },
    });
    setDeleteConfirmOpen(false);
    onClose();
  };

  return (
    <>
      <Menu
        open
        onClose={onClose}
        anchorReference="anchorPosition"
        anchorPosition={{ top: anchorPosition.y, left: anchorPosition.x }}
        slotProps={{
          paper: { sx: { minWidth: 200 } },
        }}
      >
        <MenuItem onClick={handleRename} dense>
          <ListItemIcon>
            <Iconify icon="solar:pen-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ variant: "body2" }}>
            Rename
          </ListItemText>
        </MenuItem>

        <MenuItem onClick={handleDuplicate} dense>
          <ListItemIcon>
            <Iconify icon="solar:copy-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ variant: "body2" }}>
            Duplicate
          </ListItemText>
        </MenuItem>

        <Divider />

        <MenuItem onClick={handleToggleVisibility} dense>
          <ListItemIcon>
            <Iconify
              icon={
                isShared
                  ? "solar:lock-linear"
                  : "solar:users-group-rounded-linear"
              }
              width={16}
            />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ variant: "body2" }}>
            {isShared ? "Make personal" : "Share with team"}
          </ListItemText>
        </MenuItem>

        <Divider />

        <MenuItem
          onClick={() => setDeleteConfirmOpen(true)}
          dense
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ color: "status.danger.fg" }}
        >
          <ListItemIcon sx={{ color: "inherit" }}>
            <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ variant: "body2" }}>
            Delete
          </ListItemText>
        </MenuItem>
      </Menu>

      {/* Delete confirmation dialog */}
      <Dialog
        open={deleteConfirmOpen}
        onClose={() => setDeleteConfirmOpen(false)}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 560 } }}
      >
        <DialogTitle>Delete &ldquo;{view.name}&rdquo;?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This view will be permanently removed.
            {isShared &&
              " This will remove the view for all team members."}{" "}
            This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteConfirmOpen(false)} size="small">
            Cancel
          </Button>
          <Button
            onClick={handleDeleteConfirm}
            color="error"
            variant="contained"
            size="small"
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

TabContextMenu.propTypes = {
  anchorPosition: PropTypes.shape({
    x: PropTypes.number.isRequired,
    y: PropTypes.number.isRequired,
  }),
  view: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    visibility: PropTypes.string,
  }),
  projectId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
  onRename: PropTypes.func.isRequired,
  onTabChange: PropTypes.func.isRequired,
};

export default React.memo(TabContextMenu);
