import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";

// ----------------------------------------------------------------------
// Future AGI Design System · Selection bar. Replaces the per-screen
// "N selected · actions · Cancel" bars. One look everywhere: the count in
// text, neutral ghost actions with 16px icons, Cancel last. Destructive
// actions are neutral here; red belongs to the confirm dialog they open.
// ----------------------------------------------------------------------

export default function SelectionBar({
  count,
  noun,
  label,
  actions,
  onCancel,
  sx,
}) {
  if (!count) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        height: 32,
        pl: 1.5,
        pr: 0.5,
        borderRadius: "6px",
        border: (t) =>
          `1px solid ${t.palette.border?.default ?? t.palette.divider}`,
        bgcolor: "background.paper",
        flexShrink: 0,
        ...sx,
      }}
    >
      <Typography
        sx={{
          fontSize: 13,
          fontWeight: 500,
          color: "text.primary",
          whiteSpace: "nowrap",
          pr: 0.5,
        }}
      >
        {label ??
          `${count} ${noun ? `${noun}${count === 1 ? "" : "s"} ` : ""}selected`}
      </Typography>
      <Divider orientation="vertical" flexItem sx={{ my: 0.75 }} />
      {actions
        .filter((a) => !a.hidden)
        .map((a) => {
          if (a.node)
            return <React.Fragment key={a.label}>{a.node}</React.Fragment>;
          const btn = (
            <Button
              key={a.tooltip ? undefined : a.label}
              size="small"
              variant="text"
              color="inherit"
              disabled={a.disabled}
              onClick={a.onClick}
              startIcon={
                a.icon ? <Iconify icon={a.icon} width={16} /> : undefined
              }
              sx={{
                color: "text.secondary",
                "&:hover": { color: "text.primary" },
              }}
            >
              {a.label}
            </Button>
          );
          return a.tooltip ? (
            <Tooltip key={a.label} title={a.tooltip} placement="top" arrow>
              <span>{btn}</span>
            </Tooltip>
          ) : (
            btn
          );
        })}
      <Box sx={{ flex: "0 0 4px" }} />
      <Button
        size="small"
        variant="text"
        color="inherit"
        onClick={onCancel}
        sx={{ color: "text.secondary", "&:hover": { color: "text.primary" } }}
      >
        Cancel
      </Button>
    </Stack>
  );
}

SelectionBar.propTypes = {
  count: PropTypes.number,
  noun: PropTypes.string,
  // Replaces the default "N selected" text (e.g. "All 700 matching filter").
  label: PropTypes.node,
  actions: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      icon: PropTypes.string,
      onClick: PropTypes.func,
      disabled: PropTypes.bool,
      hidden: PropTypes.bool,
      tooltip: PropTypes.string,
      node: PropTypes.node,
    }),
  ),
  onCancel: PropTypes.func,
  sx: PropTypes.object,
};

SelectionBar.defaultProps = { actions: [] };
