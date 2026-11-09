import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { createPortal } from "react-dom";
import { Paper } from "@mui/material";

export const GRID_SHELL_ID = "fa-data-grid-shell";

/**
 * The grid's bulk bar floats bottom-centre over the grid, above the status
 * bar and clear of the Falcon bubble (DG#4, MUST 4): the toolbar keeps its
 * height. Portals into the grid shell (`#fa-data-grid-shell`).
 */
export default function FloatingSelectionBar({ children }) {
  const [host, setHost] = useState(null);
  useEffect(() => {
    setHost(document.getElementById(GRID_SHELL_ID));
  }, []);
  const bar = (
    <Paper
      elevation={8}
      role="region"
      aria-label="Selected rows actions"
      sx={{
        position: host ? "absolute" : "fixed",
        left: "50%",
        transform: "translateX(-50%)",
        bottom: host ? 52 : "calc(var(--fa-fab-gutter, 0px) + 56px)",
        zIndex: 6,
        maxWidth: "calc(100% - 96px)",
        overflowX: "auto",
        borderRadius: 1.5,
        px: 1,
        border: "1px solid",
        borderColor: "divider",
        "& > *": { border: "0 !important", m: "0 !important" },
        "& .MuiButton-root": { whiteSpace: "nowrap", flexShrink: 0 },
      }}
    >
      {children}
    </Paper>
  );
  return host ? createPortal(bar, host) : bar;
}

FloatingSelectionBar.propTypes = { children: PropTypes.node };
