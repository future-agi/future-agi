import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Tooltip } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";

export default function CopyLinkButton({ text, label = "Copy", sx }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      enqueueSnackbar("Could not copy", { variant: "error" });
    }
  };

  return (
    <Tooltip title={copied ? "Copied" : label}>
      <Button
        onClick={copy}
        size="small"
        variant="outlined"
        color="primary"
        startIcon={
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={15}
          />
        }
        sx={{ flexShrink: 0, minWidth: 96, ...sx }}
      >
        {copied ? "Copied" : label}
      </Button>
    </Tooltip>
  );
}

CopyLinkButton.propTypes = {
  text: PropTypes.string,
  label: PropTypes.string,
  sx: PropTypes.object,
};
