import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";

import SvgColor from "src/components/svg-color";

// Copies `text`; the icon turns into a check for two seconds.
export default function CopyButton({ text, label = "Copy", onCopied }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      onCopied?.();
    } catch {
      setCopied(false);
    }
  };

  return (
    <Tooltip title={copied ? "Copied" : label}>
      <IconButton size="small" aria-label={label} onClick={copy}>
        <SvgColor
          src={
            copied ? "/assets/icons/ic_check.svg" : "/assets/icons/ic_copy.svg"
          }
          sx={{ width: 16, height: 16 }}
        />
      </IconButton>
    </Tooltip>
  );
}

CopyButton.propTypes = {
  text: PropTypes.string.isRequired,
  label: PropTypes.string,
  onCopied: PropTypes.func,
};
