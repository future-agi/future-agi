import { Box, IconButton, Modal } from "@mui/material";
import React from "react";
import PropType from "prop-types";
import Iconify from "../iconify";

export const ModalComponent = ({ children, open, onClose }) => {
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
          borderRadius: "12px",
        }}
      >
        <IconButton
          onClick={() => onClose()}
          sx={{ position: "absolute", top: "12px", right: "12px" }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
        {children}
      </Box>
    </Modal>
  );
};

ModalComponent.propTypes = {
  open: PropType.bool,
  onClose: PropType.func,
  children: PropType.node,
  showCross: PropType.bool,
};
