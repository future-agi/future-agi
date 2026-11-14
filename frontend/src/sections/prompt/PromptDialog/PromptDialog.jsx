import React from "react";
import PropTypes from "prop-types";
import { grey } from "src/theme/palette";
import {
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  styled,
} from "@mui/material";
import Iconify from "src/components/iconify";

const BootstrapDialog = styled(Dialog)(({ theme }) => ({
  "& .MuiDialogContent-root": {
    padding: theme.spacing("0 24px"),
  },
  "& .MuiDialogActions-root": {
    padding: theme.spacing("16px 24px"),
  },
}));

const PromptDialog = (props) => {
  const { open, handleClose, title, content, actionButtons } = props;
  return (
    <BootstrapDialog
      open={open}
      onClose={handleClose}
      aria-labelledby="alert-dialog-title"
      aria-describedby="alert-dialog-description"
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          paddingRight: "24px",
          flexDirection: "row",
        }}
      >
        <DialogTitle id="alert-dialog-title" sx={{ pb: "16px" }}>
          {title}
        </DialogTitle>
        <IconButton onClick={handleClose}>
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </Box>
      <DialogContent sx={{ color: `${grey[600]},`, mb: "20px", p: "0 24px" }}>
        {content}
      </DialogContent>
      <DialogActions>{actionButtons}</DialogActions>
    </BootstrapDialog>
  );
};

PromptDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  handleClose: PropTypes.func.isRequired,
  title: PropTypes.string.isRequired,
  content: PropTypes.node.isRequired,
  actionButtons: PropTypes.node.isRequired,
};

export default PromptDialog;
