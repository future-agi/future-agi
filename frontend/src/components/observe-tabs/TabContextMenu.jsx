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
  const { mutateAsync: deleteView } = useDeleteSavedView(projectId);
  const { mutateAsync: duplicateView } = useDuplicateSavedView(projectId);

  if (!view || !anchorPosition) return null;

  const isShared = view.visibility === "project";

  const handleRename = () => {
    onClose();
    onRename(view.id);
  };

  const handleDuplicate = () => {
    onClose();
    void duplicateView({ id: view.id })
      .then((res) => {
        const newView = res.data?.result;
        if (newView?.id) {
          onTabChange(`view-${newView.id}`);
        }
      })
      .catch(() => null);
  };

  const handleToggleVisibility = () => {
    updateView({
      id: view.id,
      visibility: isShared ? "personal" : "project",
    });
    onClose();
  };

  const handleDeleteConfirm = () => {
    setDeleteConfirmOpen(false);
    onClose();
    void deleteView(view.id)
      .then(() => {
        onTabChange("traces");
      })
      .catch(() => null);
  };

  return (
    <>
      <Menu
        open
        onClose={onClose}
        anchorReference="anchorPosition"
        anchorPosition={{ top: anchorPosition.y, left: anchorPosition.x }}
        slotProps={{
          paper: { sx: { minWidth: 180 } },
        }}
      >
        <MenuItem onClick={handleRename} dense>
          <ListItemIcon>
            <Iconify icon="mdi:pencil-outline" width={18} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ variant: "body2" }}>
            Rename
          </ListItemText>
        </MenuItem>

        <MenuItem onClick={handleDuplicate} dense>
          <ListItemIcon>
            <Iconify icon="mdi:content-copy" width={18} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ variant: "body2" }}>
            Duplicate
          </ListItemText>
        </MenuItem>

        <Divider />

        <MenuItem onClick={handleToggleVisibility} dense>
          <ListItemIcon>
            <Iconify
              icon={isShared ? "mdi:lock-outline" : "mdi:account-group-outline"}
              width={18}
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
          sx={{ color: "error.main" }}
        >
          <ListItemIcon sx={{ color: "inherit" }}>
            <Iconify icon="mdi:delete-outline" width={18} />
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
        maxWidth="xs"
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
