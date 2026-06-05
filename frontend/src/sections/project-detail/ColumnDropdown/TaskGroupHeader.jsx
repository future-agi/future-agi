import React from "react";
import PropTypes from "prop-types";
import { Box, Checkbox } from "@mui/material";
import TruncatedLabel from "src/components/truncated-label/TruncatedLabel";

// Tri-state header row for an eval-task group; row rendering stays with the caller.
const TaskGroupHeader = ({ label, checked, indeterminate, onToggle }) => {
  if (!label) return null;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: "4px",
        px: "4px",
        pt: "6px",
        pb: "2px",
        borderRadius: "4px",
        cursor: "pointer",
        "&:hover": { bgcolor: "action.hover" },
      }}
      onClick={() => onToggle?.(!checked)}
    >
      <Checkbox
        size="small"
        checked={!!checked}
        indeterminate={!!indeterminate}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => onToggle?.(e.target.checked)}
        sx={{
          p: 0,
          width: 16,
          height: 16,
          "& .MuiSvgIcon-root": { fontSize: 16 },
          "&.Mui-checked": { color: "primary.light" },
          "&.MuiCheckbox-indeterminate": { color: "primary.light" },
        }}
        inputProps={{ "aria-label": `Toggle ${label}` }}
      />
      <TruncatedLabel
        text={label}
        sx={{ fontSize: 12, fontWeight: 600, color: "text.secondary" }}
      />
    </Box>
  );
};

TaskGroupHeader.propTypes = {
  label: PropTypes.string,
  checked: PropTypes.bool,
  indeterminate: PropTypes.bool,
  onToggle: PropTypes.func,
};

export default TaskGroupHeader;
