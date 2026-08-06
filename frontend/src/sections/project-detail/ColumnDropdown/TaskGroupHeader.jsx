import React from "react";
import PropTypes from "prop-types";
import { Box, Checkbox } from "@mui/material";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Iconify from "src/components/iconify";
import TruncatedLabel from "src/components/truncated-label/TruncatedLabel";

const TaskGroupHeader = ({
  dragId,
  label,
  checked,
  indeterminate,
  onToggle,
  collapsed,
  onCollapseToggle,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: dragId || `task:${label}`, disabled: !dragId });

  if (!label) return null;

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : 1,
  };

  return (
    <Box
      ref={setNodeRef}
      style={style}
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
        opacity: isDragging ? 0.6 : 1,
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
      {dragId && (
        <Box
          {...attributes}
          {...listeners}
          onClick={(e) => e.stopPropagation()}
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 16,
            height: 16,
            cursor: "grab",
            color: "text.disabled",
            "&:active": { cursor: "grabbing" },
            flexShrink: 0,
          }}
        >
          <Iconify icon="mdi:dots-grid" width={14} />
        </Box>
      )}
      <TruncatedLabel
        text={label}
        sx={{ fontSize: 12, fontWeight: 600, color: "text.secondary" }}
      />
      {onCollapseToggle && (
        <Box
          onClick={(e) => {
            e.stopPropagation();
            onCollapseToggle();
          }}
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 16,
            height: 16,
            ml: "auto",
            flexShrink: 0,
            cursor: "pointer",
            color: "text.disabled",
            "&:hover": { color: "text.secondary" },
          }}
          aria-label={collapsed ? `Expand ${label}` : `Collapse ${label}`}
        >
          <Iconify
            icon={collapsed ? "mdi:chevron-right" : "mdi:chevron-down"}
            width={16}
          />
        </Box>
      )}
    </Box>
  );
};

TaskGroupHeader.propTypes = {
  dragId: PropTypes.string,
  label: PropTypes.string,
  checked: PropTypes.bool,
  indeterminate: PropTypes.bool,
  onToggle: PropTypes.func,
  collapsed: PropTypes.bool,
  onCollapseToggle: PropTypes.func,
};

export default TaskGroupHeader;
