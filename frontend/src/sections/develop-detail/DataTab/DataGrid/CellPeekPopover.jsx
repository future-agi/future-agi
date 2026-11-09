import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Popover, Stack, Typography } from "@mui/material";
import CopyButton from "src/sections/common/ux/CopyButton";
import { KeyCap } from "src/sections/common/ux/KeyCap";
import { fmtCount } from "src/sections/common/format/fmt";

const PEEK_CHARS = 600;

/** `{text, mono, more}` for a cell value (pretty JSON when it parses). */
export function peekText(value) {
  let v = value;
  if (v && typeof v === "object" && "cell_value" in v) v = v.cell_value;
  if (v == null || v === "") return { text: "", mono: false, more: 0 };
  let text = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  let mono = typeof v !== "string";
  if (typeof v === "string" && /^\s*[[{]/.test(v)) {
    try {
      text = JSON.stringify(JSON.parse(v), null, 2);
      mono = true;
    } catch {
      // plain text
    }
  }
  return {
    text: text.slice(0, PEEK_CHARS),
    mono,
    more: Math.max(0, text.length - PEEK_CHARS),
  };
}

/** Space on a focused cell (DG#7): the value, Copy, Open row (Enter). */
export default function CellPeekPopover({ peek, onClose, onOpenRow }) {
  const p = peek ? peekText(peek.value) : null;
  return (
    <Popover
      open={Boolean(peek)}
      anchorEl={peek?.anchor ?? null}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      PaperProps={{
        sx: { width: 420, maxWidth: "calc(100vw - 32px)", p: 1.5 },
      }}
    >
      {p && (
        <Stack gap={1}>
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography
              typography="s2"
              fontWeight={600}
              sx={{ flex: 1 }}
              noWrap
            >
              {peek.column}
            </Typography>
            <CopyButton value={p.text} tooltip="Copy value" />
          </Stack>
          <Box
            sx={{
              maxHeight: 260,
              overflow: "auto",
              typography: "s2",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontFamily: p.mono ? "monospace" : undefined,
              color: p.text ? "text.primary" : "text.disabled",
            }}
          >
            {p.text || "Empty"}
          </Box>
          {p.more > 0 && (
            <Typography typography="s3" color="text.secondary">
              +{fmtCount(p.more, "char")} · open the row for all of it
            </Typography>
          )}
          <Stack direction="row" justifyContent="flex-end">
            <Button size="small" onClick={onOpenRow} sx={{ gap: 0.75 }}>
              Open row <KeyCap>↵</KeyCap>
            </Button>
          </Stack>
        </Stack>
      )}
    </Popover>
  );
}

CellPeekPopover.propTypes = {
  peek: PropTypes.object,
  onClose: PropTypes.func,
  onOpenRow: PropTypes.func,
};
