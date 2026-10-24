import { Box, Button, Divider, Modal, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import SelectionBar from "src/components/selection-bar";
import { trackEvent, Events } from "src/utils/Mixpanel";
import { useRunAnnotationsStore } from "../states";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const DevelopAnnotationRightSection = ({
  selectedRowsCount,
  onDeleteSelected,
  onCancelSelection,
}) => {
  const [openModal, setOpenModal] = useState(false); // State to manage modal
  const { setOpenRunAnnotations } = useRunAnnotationsStore();
  const handleOpenModal = () => {
    setOpenModal(true); // Open modal
    trackEvent(Events.annDelInit);
  };
  const handleCloseModal = () => setOpenModal(false); // Close modal
  const { role } = useAuthContext();

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: "12px" }}>
      {selectedRowsCount > 0 ? (
        <SelectionBar
          count={selectedRowsCount}
          onCancel={onCancelSelection}
          actions={[
            {
              label: "Delete",
              icon: "solar:trash-bin-trash-linear",
              onClick: handleOpenModal,
              disabled: !RolePermission.DATASETS[PERMISSIONS.DELETE][role],
            },
          ]}
        />
      ) : (
        <>
          {/* <IconButton size="small" sx={{ color: "text.secondary" }}>
            <Iconify icon="solar:magnifier-linear" />
          </IconButton>
          <IconButton
            size="small"
            sx={{ color: "text.secondary" }}
            onClick={() => {}}
          >
            <Iconify icon="solar:filter-linear" />
          </IconButton> */}

          <Divider orientation="vertical" flexItem />
          <Button
            variant="contained"
            color="primary"
            size="small"
            // style={{
            //   backgroundColor: primary.main,
            //   fontSize: "12px",
            //   color: "common.white",
            //   cursor: "pointer",
            // }}
            onClick={() => {
              trackEvent(Events.annNewViewClick);
              setOpenRunAnnotations(true);
            }}
            disabled={!RolePermission.DATASETS[PERMISSIONS.CREATE][role]}
          >
            Create New View
          </Button>
        </>
      )}

      {/* Delete Modal */}
      <DeleteModal
        open={openModal}
        onClose={handleCloseModal}
        onDeleteSelected={() => {
          onDeleteSelected(); // Call delete function
          handleCloseModal(); // Close modal
        }}
        onCancelSelection={handleCloseModal} // Close modal on cancel
      />
    </Box>
  );
};

const DeleteModal = ({
  open,
  onClose,
  onDeleteSelected,
  onCancelSelection,
}) => {
  return (
    <Modal open={open} onClose={onClose}>
      <Box
        sx={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: 400,
          bgcolor: "background.paper",
          // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
          boxShadow: 24,
          borderRadius: "8px",
          p: 3,
        }}
      >
        <Typography
          variant="h6"
          sx={{ fontWeight: "600", marginBottom: "16px" }}
        >
          Delete View
        </Typography>
        <Typography sx={{ marginBottom: "24px", color: "text.secondary" }}>
          Are you sure you want to delete the selected annotation view(s)?
        </Typography>
        <Box sx={{ display: "flex", justifyContent: "flex-end", gap: "12px" }}>
          <Button
            onClick={onCancelSelection}
            sx={{
              color: "text.secondary",
            }}
          >
            Cancel
          </Button>
          <Button
            onClick={onDeleteSelected}
            variant="contained"
            color="error"
          >
            Delete
          </Button>
        </Box>
      </Box>
    </Modal>
  );
};

DeleteModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onDeleteSelected: PropTypes.func.isRequired,
  onCancelSelection: PropTypes.func.isRequired,
};

DevelopAnnotationRightSection.propTypes = {
  selectedRowsCount: PropTypes.number.isRequired,
  onDeleteSelected: PropTypes.func.isRequired,
  onCancelSelection: PropTypes.func.isRequired,
};

export default DevelopAnnotationRightSection;
