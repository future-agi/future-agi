// IT-R2 G4 P5 · the Graph recommendation as one quiet line under the view
// tabs (never a tooltip over the legend): `Graph recommended · Support
// Agent v3 retried lookup_order 2×  [Open Graph]  ×`. Dismissed per reason
// (remembered in this browser); hidden once Graph is open.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const KEY = "fa.trajectory.graphHintDismissed";
const read = () => {
  try {
    return JSON.parse(window.localStorage.getItem(KEY) ?? "[]");
  } catch {
    return [];
  }
};

export default function RecommendHint({ text, onOpen, sx }) {
  const [dismissed, setDismissed] = useState(read);
  if (!text || dismissed.includes(text)) return null;
  const dismiss = () => {
    const next = [...dismissed, text].slice(-50);
    setDismissed(next);
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* private window: dismissed for this view only */
    }
  };
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      data-testid="graph-recommend-hint"
      sx={{
        px: 1.5,
        height: 26,
        flexShrink: 0,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        minWidth: 0,
        ...sx,
      }}
    >
      <Box
        component="span"
        sx={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          bgcolor: "primary.main",
          flexShrink: 0,
        }}
      />
      <Typography
        typography="s2"
        color="text.secondary"
        noWrap
        title={text}
        sx={{ minWidth: 0 }}
      >
        {text}
      </Typography>
      {onOpen ? (
        <Button
          size="small"
          onClick={onOpen}
          sx={{ typography: "s2", py: 0, minWidth: 0, flexShrink: 0 }}
        >
          Open Graph
        </Button>
      ) : null}
      <Box sx={{ flex: 1 }} />
      <IconButton
        size="small"
        aria-label="Dismiss the graph recommendation"
        onClick={dismiss}
        sx={{ p: 0.25, flexShrink: 0 }}
      >
        <Iconify icon="mingcute:close-line" width={14} />
      </IconButton>
    </Stack>
  );
}

RecommendHint.propTypes = {
  text: PropTypes.string,
  onOpen: PropTypes.func,
  sx: PropTypes.object,
};
