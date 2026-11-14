import { Box, Button, Chip } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

/**
 * Base selection actions component for execution grids
 * Displays selected count with Run Evals and Cancel actions
 *
 * @param {Object} props
 * @param {number} props.selectedCount - Number of selected items
 * @param {Function} props.onRunEvals - Callback when "Run Evals" is clicked
 * @param {Function} props.onDelete - Callback when "Delete" is clicked
 * @param {Function} props.onClearSelection - Callback when "Cancel" is clicked
 */
const BaseSelectionActions = ({
  selectedCount,
  onRunEvals,
  onDelete,
  onClearSelection,
}) => {
  if (selectedCount === 0) return null;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
      }}
    >
      <Chip
        label={`${selectedCount} selected`}
        size="small"
        sx={{
          color: "status.discovery.fg",
          borderColor: "status.discovery.solid",
          backgroundColor: "status.discovery.bg",

          ":hover": {
            backgroundColor: "status.discovery.bg",
          },
        }}
      />
      <Button
        size="small"
        variant="outlined"
        startIcon={<Iconify icon="solar:play-linear" width={16} />}
        onClick={onRunEvals}
        sx={{
          whiteSpace: "nowrap",
          color: "primary.main",
          borderColor: "primary.main",
          "&:hover": {
            borderColor: "primary.main",
            backgroundColor: "action.hover",
          },
        }}
      >
        Run Evals
      </Button>
      <Button
        size="small"
        variant="outlined"
        startIcon={
          <SvgColor
            width={2}
            // @ts-ignore
            sx={{ mt: -0.5 }}
            src={"/assets/icons/ic_delete.svg"}
          />
        }
        onClick={onDelete}
        sx={{
          whiteSpace: "nowrap",
          "&:hover": {
            borderColor: "status.discovery.solid",
            backgroundColor: "status.discovery.bg",
          },
        }}
      >
        Delete
      </Button>
      <Button
        size="small"
        variant="text"
        onClick={onClearSelection}
        sx={{
          color: "text.secondary",
          "&:hover": {
            backgroundColor: "action.hover",
          },
        }}
      >
        Cancel
      </Button>
    </Box>
  );
};

BaseSelectionActions.propTypes = {
  selectedCount: PropTypes.number.isRequired,
  onRunEvals: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
  onClearSelection: PropTypes.func.isRequired,
};

export default BaseSelectionActions;
