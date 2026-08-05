import React, { useState } from "react";
import PropTypes from "prop-types";
import { Stack, Typography, IconButton, Tooltip } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";

export function InviteLinkCell({ data }) {
  const [copied, setCopied] = useState(false);
  const link = data?.invite_link;

  if (!link) {
    return (
      <Typography
        variant="s2"
        color="text.disabled"
        sx={{ lineHeight: "40px" }}
      >
        —
      </Typography>
    );
  }

  const copy = async (e) => {
    e?.stopPropagation();
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      enqueueSnackbar("Could not copy", { variant: "error" });
    }
  };

  return (
    <Stack
      direction="row"
      spacing={0.5}
      alignItems="center"
      sx={{ height: "100%", minWidth: 0 }}
    >
      <Tooltip title={copied ? "Copied" : "Copy invite link"}>
        <IconButton size="small" onClick={copy} sx={{ color: "primary.main" }}>
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={16}
          />
        </IconButton>
      </Tooltip>
      <Typography
        variant="s2"
        onClick={copy}
        sx={{
          fontFamily: "monospace",
          color: "text.secondary",
          cursor: "pointer",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          minWidth: 0,
        }}
      >
        {link}
      </Typography>
    </Stack>
  );
}

InviteLinkCell.propTypes = {
  data: PropTypes.object,
};
