import React from "react";
import HelpKnowledgeBase from "./HelpKnowledgeBase";
import PropTypes from "prop-types";
import {
  Box,
  Dialog,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

const InfoKnowledgeModal = ({ open, onClose, onCreateKnowledge }) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth={false}
      PaperProps={{
        width: "720px",
        maxWidth: "none",
      }}
    >
      <Box sx={{ padding: 2 }}>
        <DialogTitle sx={{ padding: 0, margin: 0 }}>
          <Box display="flex" justifyContent={"space-between"}>
            <Typography
              // @ts-ignore
              variant="m1"
              fontWeight={"fontWeightSemiBold"}
              color="text.primary"
            >
              Steps to create knowledge base
            </Typography>
            <IconButton onClick={onClose}>
              <Iconify icon="solar:close-linear" color="text.primary" />
            </IconButton>
          </Box>
        </DialogTitle>
        <HelpKnowledgeBase
          helpIcon={true}
          onCreateKnowledge={onCreateKnowledge}
        />
      </Box>
    </Dialog>
  );
};

export default InfoKnowledgeModal;

InfoKnowledgeModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onCreateKnowledge: PropTypes.func,
};
