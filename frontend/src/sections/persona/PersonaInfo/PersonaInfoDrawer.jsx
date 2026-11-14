import { Box, Drawer, IconButton } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import PersonaInfo from "./PersonaInfo";
import Iconify from "src/components/iconify";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const PersonaInfoDrawer = ({
  open,
  onClose,
  editPersona,
  setSelectedTab,
  persona,
}) => {
  const handleOnSuccess = () => {
    if (!editPersona) {
      setSelectedTab("custom");
    }
    onClose();
  };
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={coveredDrawerSx(covered)}
    >
      <Box sx={{ width: 640, height: "100vh", position: "relative" }}>
        <IconButton
          onClick={onClose}
          sx={{
            position: "absolute",
            top: "12px",
            right: "12px",
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" width={16} />
        </IconButton>
        <PersonaInfo
          editPersona={editPersona}
          onSuccess={handleOnSuccess}
          onCancel={onClose}
          persona={persona}
        />
      </Box>
    </Drawer>
  );
};

PersonaInfoDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  editPersona: PropTypes.object,
  setSelectedTab: PropTypes.func,
  persona: PropTypes.object,
};

export default PersonaInfoDrawer;
