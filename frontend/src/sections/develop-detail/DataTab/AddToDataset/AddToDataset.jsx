import React from "react";
import PropTypes from "prop-types";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { styled } from "@mui/system";

const StyledIconButton = styled(IconButton)({
  position: "absolute",
  top: "12px",
  right: "12px",
});

const AddToDataset = ({ title, content, actionButton, open, onClose }) => {
  return (
    <Dialog open={open} onClose={onClose} fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <StyledIconButton onClick={onClose}>
        <Iconify icon="mingcute:close-line" />
      </StyledIconButton>
      <DialogContent>{content}</DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="primary">
          {actionButton}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

AddToDataset.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  actionButton: PropTypes.node,
  title: PropTypes.node || PropTypes.string,
  content: PropTypes.node,
};

export default AddToDataset;
