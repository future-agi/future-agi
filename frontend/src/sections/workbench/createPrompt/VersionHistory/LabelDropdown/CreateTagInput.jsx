import { Box, CircularProgress, IconButton, TextField } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "src/components/svg-color";

const FIELD_HEIGHT = 36;

const squareButtonSx = {
  width: FIELD_HEIGHT,
  height: FIELD_HEIGHT,
  flexShrink: 0,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "6px",
};

const CreateTagInput = ({
  newLabelName,
  setNewLabelName,
  handleCreateLabel,
  isCreatingLabel,
  onCancel,
}) => {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mt: 0.75 }}>
      <TextField
        size="small"
        fullWidth
        autoFocus
        placeholder="Enter tag name"
        value={newLabelName}
        onChange={(e) => setNewLabelName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            // Guard double-submit; input stays enabled to keep focus.
            if (!isCreatingLabel) handleCreateLabel();
          } else if (e.key === "Escape") {
            // Close only the input, not the modal.
            e.stopPropagation();
            onCancel();
          }
        }}
        sx={{
          "& .MuiOutlinedInput-root": {
            height: FIELD_HEIGHT,
            fontSize: "13px",
          },
        }}
      />
      <IconButton
        size="small"
        color="primary"
        onClick={handleCreateLabel}
        disabled={!newLabelName.trim() || isCreatingLabel}
        sx={squareButtonSx}
      >
        {isCreatingLabel ? (
          <CircularProgress size={16} />
        ) : (
          <SvgColor
            src="/assets/icons/ic_check.svg"
            sx={{ width: 18, height: 18 }}
          />
        )}
      </IconButton>
      <IconButton
        size="small"
        onClick={onCancel}
        disabled={isCreatingLabel}
        sx={squareButtonSx}
      >
        <SvgColor
          src="/assets/icons/ic_close.svg"
          sx={{ width: 18, height: 18 }}
        />
      </IconButton>
    </Box>
  );
};

CreateTagInput.propTypes = {
  newLabelName: PropTypes.string.isRequired,
  setNewLabelName: PropTypes.func.isRequired,
  handleCreateLabel: PropTypes.func.isRequired,
  isCreatingLabel: PropTypes.bool.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export default CreateTagInput;
