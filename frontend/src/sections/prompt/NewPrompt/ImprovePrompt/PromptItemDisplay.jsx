import { Typography, Box, Tooltip, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import React from "react";
import PropTypes from "prop-types";

const PromptItemDisplay = ({ item, index, loading, handleEditClick }) => (
  <Typography
    variant="subtitle2"
    sx={{
      color: "text.primary",
      background: "action.hover",
      padding: 1.6,
      paddingBottom: item.editable ? 4 : 1.6,
      // maxHeight:"200px",
      overflowY: "auto",
      paddingRight: 2,
      borderRadius: "8px",
      fontWeight: "400",
      wordWrap: "break-word",
      position: "relative",
    }}
  >
    {item.prompt}
    {item.editable ? (
      <Box
        sx={{
          position: "absolute",
          bottom: 0,
          right: 0,
          padding: 0.4,
          paddingRight: 1,
          cursor: loading ? "not-allowed" : "pointer",
        }}
      >
        <Tooltip title="Edit this Prompt">
          <span>
            <IconButton
              aria-label="edit-prompt"
              onClick={loading ? () => {} : () => handleEditClick(index)}
            >
              <Iconify
                icon="solar:pen-linear"
                color="text.disabled"
                style={{ pointerEvents: loading ? "none" : "auto" }}
              />
            </IconButton>
          </span>
        </Tooltip>
      </Box>
    ) : null}
  </Typography>
);

PromptItemDisplay.propTypes = {
  item: PropTypes.object,
  index: PropTypes.number,
  loading: PropTypes.bool,
  handleEditClick: PropTypes.func,
};

export default PromptItemDisplay;
