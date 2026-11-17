import React from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Button } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import KeyHintTooltip from "../KeyHintTooltip";

/**
 * OutlineToggle — Excel's outline `+` / `−` (ONE-VIEW-SPEC §3.4): a 20 px
 * square, tooltip `Expand <label>` / `Collapse <label>`, `aria-expanded`.
 * Keys when focused: → opens, ← closes, Enter / Space toggle.
 *
 * Props: `{open, onToggle, label, count?, controls?, sx?}` — `count` shows
 * a small number after the square (`+ 3`), `controls` is the id of the
 * region it opens.
 */
export default function OutlineToggle({
  open,
  onToggle,
  label,
  count,
  controls,
  sx,
}) {
  const verb = open ? "Collapse" : "Expand";
  return (
    <CustomTooltip
      show
      size="small"
      placement="top"
      title={`${verb} ${label}`}
      disableInteractive
    >
      <ButtonBase
        aria-expanded={open}
        aria-controls={controls}
        aria-label={`${verb} ${label}`}
        onClick={(e) => {
          e.stopPropagation();
          onToggle?.(e);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight" && !open) {
            e.preventDefault();
            onToggle?.(e);
          } else if (e.key === "ArrowLeft" && open) {
            e.preventDefault();
            onToggle?.(e);
          }
        }}
        sx={{
          gap: 0.5,
          borderRadius: 0.5,
          color: "text.secondary",
          flexShrink: 0,
          "&:hover .fa-outline-box": {
            borderColor: "text.secondary",
            color: "text.primary",
          },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 1,
          },
          ...sx,
        }}
      >
        <Box
          className="fa-outline-box"
          component="span"
          aria-hidden
          sx={{
            width: 20,
            height: 20,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 14,
            lineHeight: 1,
            fontWeight: 600,
            bgcolor: "background.paper",
          }}
        >
          {open ? "−" : "+"}
        </Box>
        {count != null && (
          <Box component="span" sx={{ typography: "s3", pr: 0.25 }}>
            {count}
          </Box>
        )}
      </ButtonBase>
    </CustomTooltip>
  );
}

OutlineToggle.propTypes = {
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  label: PropTypes.string.isRequired,
  count: PropTypes.number,
  controls: PropTypes.string,
  sx: PropTypes.object,
};

/**
 * ExpandAllButton — text button `Expand all` / `Collapse all` for an
 * outline; the tooltip names the key of the given keymap actions.
 *
 * Props: `{outline, noun = "steps", expandAction = "expandAllSteps",
 * collapseAction = "collapseAllSteps", sx?}`.
 */
export function ExpandAllButton({
  outline,
  noun = "steps",
  expandAction = "expandAllSteps",
  collapseAction = "collapseAllSteps",
  sx,
}) {
  const collapse = outline.allOpen;
  return (
    <KeyHintTooltip
      title={collapse ? `Collapse all ${noun}` : `Expand all ${noun}`}
      action={collapse ? collapseAction : expandAction}
    >
      <Button
        size="small"
        variant="text"
        color="inherit"
        onClick={collapse ? outline.collapseAll : outline.expandAll}
        sx={{ typography: "s2", color: "text.secondary", minWidth: 0, ...sx }}
      >
        {collapse ? "Collapse all" : "Expand all"}
      </Button>
    </KeyHintTooltip>
  );
}

ExpandAllButton.propTypes = {
  outline: PropTypes.shape({
    allOpen: PropTypes.bool,
    expandAll: PropTypes.func,
    collapseAll: PropTypes.func,
  }).isRequired,
  noun: PropTypes.string,
  expandAction: PropTypes.string,
  collapseAction: PropTypes.string,
  sx: PropTypes.object,
};
