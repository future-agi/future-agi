import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * The same action as a command — next to a button that starts something in
 * the UI (Start improvement, Build environment, Re-run failing) or on a run
 * that was started from code. Hover shows the command; a click copies it.
 */
export default function CopyCommandButton({
  command,
  label = "Copy as command",
  sx,
}) {
  const [copied, setCopied] = useState(false);
  const copy = (e) => {
    e?.stopPropagation?.();
    navigator.clipboard?.writeText(command).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Tooltip
      arrow
      title={
        <Box
          component="span"
          sx={{ fontFamily: "monospace", whiteSpace: "pre-wrap" }}
        >
          {command}
        </Box>
      }
    >
      <Button
        size="small"
        onClick={copy}
        startIcon={
          <Iconify
            icon={copied ? "solar:check-circle-linear" : "solar:code-linear"}
            width={14}
          />
        }
        sx={{
          color: "text.secondary",
          whiteSpace: "nowrap",
          flexShrink: 0,
          ...sx,
        }}
      >
        {copied ? "Copied" : label}
      </Button>
    </Tooltip>
  );
}

CopyCommandButton.propTypes = {
  command: PropTypes.string.isRequired,
  label: PropTypes.string,
  sx: PropTypes.object,
};
