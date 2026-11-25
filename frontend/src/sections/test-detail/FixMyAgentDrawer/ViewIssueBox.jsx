import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
} from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "../../../components/iconify";

const ViewIssueBox = ({ open, onClose, breakdown, description }) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle
        sx={{
          padding: 2,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography variant="m3" fontWeight="fontWeightSemiBold">
          Evidence for this suggestion
        </Typography>
        <IconButton onClick={onClose}>
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: "16px !important",
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column" }}>
          <Typography variant="m3" fontWeight="fontWeightSemiBold">
            Problem Analysis
          </Typography>
          <ul style={{ margin: 0, paddingLeft: "18px" }}>
            {breakdown.map((item) => (
              <li key={item}>
                <Typography variant="s1" component="div">
                  {item}
                </Typography>
              </li>
            ))}
          </ul>
        </Box>
        <Box sx={{ display: "flex", flexDirection: "column" }}>
          <Typography variant="m3" fontWeight="fontWeightSemiBold">
            Solution
          </Typography>
          <Typography variant="s1">{description}</Typography>
        </Box>
      </DialogContent>
    </Dialog>
  );
};

ViewIssueBox.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  breakdown: PropTypes.array.isRequired,
  description: PropTypes.string.isRequired,
};

export default ViewIssueBox;
