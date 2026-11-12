import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";

import SandboxIcon from "./SandboxIcon";

const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

const COPIED = "Copied";
const FAILED = "Couldn't copy. This browser blocked the clipboard.";

/**
 * Copies a sandbox's full id. It sits inside a clickable row or card, so a
 * click or a key press on it never opens the sandbox. The result is said in
 * the tooltip and to screen readers.
 */
export default function CopyIdButton({ value, labelled = false, sx }) {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    if (!status) return undefined;
    const timer = setTimeout(() => setStatus(null), 1600);
    return () => clearTimeout(timer);
  }, [status]);

  const copy = async (event) => {
    event.stopPropagation();
    try {
      if (
        window.isSecureContext === false ||
        typeof navigator.clipboard?.writeText !== "function"
      ) {
        throw new Error("No clipboard");
      }
      await navigator.clipboard.writeText(value);
      setStatus(COPIED);
    } catch {
      setStatus(FAILED);
    }
  };
  const common = {
    onClick: copy,
    onKeyDown: (event) => event.stopPropagation(),
    "aria-label": "Copy sandbox id",
    "data-testid": "copy-sandbox-id",
  };
  const icon = (
    <SandboxIcon name={status === COPIED ? "check" : "copy"} size={14} />
  );

  return (
    <>
      <Tooltip title={status || "Copy id"} arrow>
        {labelled ? (
          <Button
            size="small"
            color="inherit"
            startIcon={icon}
            sx={{ color: "text.secondary", typography: "s2", ...sx }}
            {...common}
          >
            Copy id
          </Button>
        ) : (
          <IconButton
            size="small"
            sx={{ p: 0.25, color: "text.secondary", ...sx }}
            {...common}
          >
            {icon}
          </IconButton>
        )}
      </Tooltip>
      <Box component="span" role="status" sx={visuallyHidden}>
        {status || ""}
      </Box>
    </>
  );
}

CopyIdButton.propTypes = {
  value: PropTypes.string.isRequired,
  /** A text button ("Copy id") instead of the icon alone. */
  labelled: PropTypes.bool,
  sx: PropTypes.object,
};
