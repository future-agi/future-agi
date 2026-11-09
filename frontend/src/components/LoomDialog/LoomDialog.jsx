import ExternalContentFrame from "src/components/external-content/ExternalContentFrame";
import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "../iconify";

const LoomDialog = ({ open, onClose, title, loomUrl }) => {
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle sx={{ padding: 2 }}>{title}</DialogTitle>
      <IconButton
        onClick={onClose}
        sx={{
          position: "absolute",
          top: "12px",
          right: "12px",
          color: "text.primary",
        }}
      >
        <Iconify icon="akar-icons:cross" />
      </IconButton>
      <DialogContent sx={{ padding: 2, paddingTop: 0 }}>
        <Box
          sx={{
            position: "relative",
            paddingBottom: "58.189655172413794%",
            height: 0,
          }}
        >
          <ExternalContentFrame
            src={loomUrl}
            frameBorder="0"
            title={title}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: "100%",
            }}
          ></ExternalContentFrame>
        </Box>
      </DialogContent>
    </Dialog>
  );
};

LoomDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  title: PropTypes.string,
  loomUrl: PropTypes.string,
};

export default LoomDialog;
