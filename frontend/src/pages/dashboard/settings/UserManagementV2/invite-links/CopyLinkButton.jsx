import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Tooltip } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";

export default function CopyLinkButton({ link }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      enqueueSnackbar("Could not copy", { variant: "error" });
    }
  };

  return (
    <Tooltip title={copied ? "Copied" : "Copy link"}>
      <Button
        onClick={copy}
        size="small"
        variant={copied ? "contained" : "outlined"}
        color={copied ? "success" : "primary"}
        startIcon={
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={15}
          />
        }
        sx={{ flexShrink: 0, minWidth: 96 }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </Tooltip>
  );
}

CopyLinkButton.propTypes = { link: PropTypes.string };
