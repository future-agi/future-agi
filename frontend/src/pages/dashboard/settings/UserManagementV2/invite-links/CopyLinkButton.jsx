import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";

export default function CopyLinkButton({ text, label = "Copy", sx }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!(await copyToClipboard(text))) {
      enqueueSnackbar("Could not copy", { variant: "error" });
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <CustomTooltip show size="small" title={copied ? "Copied" : label}>
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
    </CustomTooltip>
  );
}

CopyLinkButton.propTypes = {
  text: PropTypes.string,
  label: PropTypes.string,
  sx: PropTypes.object,
};
