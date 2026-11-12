// W1-1: the row view's History of one re-run cell (Result 1 · Result 2 …,
// the current one last), from R3 `trials_detail[].history`. Absent for a
// cell that was never re-run.
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Popover, Stack, Typography } from "@mui/material";
import { historyButtonText, historyEntries } from "./cellHistoryModel";

const clip = (v) => {
  const s = typeof v === "string" ? v : v == null ? "" : JSON.stringify(v);
  return s.length > 220 ? `${s.slice(0, 220)}…` : s;
};

export default function CellHistory({ trial, evalKeys }) {
  const [anchor, setAnchor] = useState(null);
  const text = historyButtonText(trial);
  if (!text) return null;
  const entries = historyEntries(trial, evalKeys);
  return (
    <>
      <Button
        size="small"
        variant="text"
        data-testid="cell-history"
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{ typography: "s3", minWidth: 0, px: 0.75, whiteSpace: "nowrap" }}
      >
        {text}
      </Button>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { p: 1.5, width: 380, maxHeight: 420 } } }}
      >
        <Stack spacing={1} data-testid="cell-history-list">
          {[...entries].reverse().map((e) => (
            <Box
              key={e.n}
              sx={{
                border: "1px solid",
                borderColor: e.now ? "primary.light" : "divider",
                borderRadius: 0.5,
                p: 1,
              }}
            >
              <Stack direction="row" gap={1} alignItems="baseline">
                <Typography typography="s2" fontWeight="fontWeightMedium">
                  {e.label}
                </Typography>
                <Typography typography="s3" color="text.secondary" noWrap>
                  {e.meta}
                </Typography>
              </Stack>
              {e.scores ? (
                <Typography typography="s3" color="text.secondary">
                  {e.scores}
                </Typography>
              ) : null}
              <Typography
                typography="s2"
                sx={{ mt: 0.5, wordBreak: "break-word" }}
                color={e.status === "error" ? "red.700" : "text.primary"}
              >
                {e.status === "error"
                  ? `Error · ${e.error ?? "the try failed"}`
                  : clip(e.output)}
              </Typography>
            </Box>
          ))}
        </Stack>
      </Popover>
    </>
  );
}

CellHistory.propTypes = {
  trial: PropTypes.object,
  evalKeys: PropTypes.arrayOf(PropTypes.string),
};
