// W5-8 (EV-21): a judge reason (or a comment body) as safe Markdown with a
// Raw toggle, keeping the existing Copy reason button when asked. The copy
// is always the raw text; exports keep the raw text too (W3 B).
import React from "react";
import PropTypes from "prop-types";
import { IconButton, Stack } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import SafeMarkdown from "./SafeMarkdown";

export default function ReasonText({
  text,
  copy = false,
  copyLabel = "Copy reason",
}) {
  if (!text) return null;
  return (
    <Stack direction="row" alignItems="flex-start" gap={0.5}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <SafeMarkdown text={text} />
      </div>
      {copy && (
        <IconButton
          size="small"
          aria-label={copyLabel}
          onClick={() => {
            copyToClipboard(text);
            enqueueSnackbar("Reason copied", { variant: "success" });
          }}
          sx={{ p: 0.25 }}
        >
          <Iconify icon="basil:copy-outline" width={14} />
        </IconButton>
      )}
    </Stack>
  );
}

ReasonText.propTypes = {
  text: PropTypes.string,
  copy: PropTypes.bool,
  copyLabel: PropTypes.string,
};
