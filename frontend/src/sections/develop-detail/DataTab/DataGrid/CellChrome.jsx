import React, { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Popover,
  Stack,
  Typography,
  alpha,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useChangeSetStore } from "../Versions/changeSetStore";
import { useDataGridView } from "./useDataGridView";
import { WRAP_MAX_HEIGHT } from "./dataGridView";
import useTypedCell from "src/sections/common/Values/gridTyped";

// Versioned-grid chrome around a cell (area-datasets G4, G10):
//  - pending edits: warning tint, 6 px amber corner, "Edited · was: “…”"
//  - Compact density: one line with an ellipsis; ⤢ on a truncated cell opens
//    the full value with Copy
//  - Wrap density: the whole value, capped at 320 px with a fade

const TEXTUAL_TYPES = new Set(["text", "json", "array"]);
const TEXTUAL_ORIGINS = new Set(["run_prompt", "evaluation_reason", "OTHERS"]);

const EMPTY_JSON = new Set(["[]", "{}", "null"]);

const toText = (value) => {
  if (value == null) return "";
  if (typeof value === "string")
    return EMPTY_JSON.has(value.trim()) ? "" : value;
  if (Array.isArray(value) && !value.length) return "";
  if (typeof value === "object" && !Object.keys(value).length) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const prettyText = (value) => {
  const text = toText(value);
  const trimmed = text.trim();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      return text;
    }
  }
  return text;
};

const clip = (s, max = 140) => (s.length > max ? `${s.slice(0, max - 1)}…` : s);

/** Whether this cell is plain text the compact/wrap renderers can show. */
export function isTextualCell({ dataType, originType, status }) {
  if (status === "error" || status === "running") return false;
  if (originType === "evaluation") return false;
  return (
    TEXTUAL_TYPES.has(dataType) && TEXTUAL_ORIGINS.has(originType ?? "OTHERS")
  );
}

/** Full value + Copy (max 600×400). */
export const ValuePopover = ({ anchorEl, onClose, title, value }) => {
  const text = prettyText(value);
  return (
    <Popover
      open={Boolean(anchorEl)}
      anchorEl={anchorEl}
      onClose={onClose}
      onClick={(e) => e.stopPropagation()}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      PaperProps={{ sx: { width: 600, maxWidth: "90vw", maxHeight: 400 } }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ px: 2, py: 1, borderBottom: 1, borderColor: "divider" }}
      >
        <Typography typography="s2" fontWeight="fontWeightSemiBold" noWrap>
          {title}
        </Typography>
        <Stack direction="row" alignItems="center" gap={0.5}>
          <Typography typography="s3" color="text.disabled">
            {text.length.toLocaleString()} chars
          </Typography>
          <Button
            size="small"
            startIcon={<Iconify icon="mdi:content-copy" width={14} />}
            onClick={() => {
              navigator.clipboard?.writeText(text);
              enqueueSnackbar("Copied", { variant: "success" });
            }}
            sx={{ typography: "s2" }}
          >
            Copy
          </Button>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="mingcute:close-line" width={16} />
          </IconButton>
        </Stack>
      </Stack>
      <Box
        component="pre"
        sx={{
          m: 0,
          px: 2,
          py: 1.5,
          overflow: "auto",
          maxHeight: 340,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          typography: "s2",
          fontFamily:
            text.trim().startsWith("{") || text.trim().startsWith("[")
              ? "monospace"
              : "inherit",
        }}
      >
        {text || "Empty"}
      </Box>
    </Popover>
  );
};

ValuePopover.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  title: PropTypes.string,
  value: PropTypes.any,
};

/** One-line (Compact) or wrapped (Wrap) text with the ⤢ expander. */
const TextValue = ({ value, wrap, title, json }) => {
  const ref = useRef(null);
  const [truncated, setTruncated] = useState(false);
  const [anchor, setAnchor] = useState(null);
  const text = toText(value);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setTruncated(
      wrap
        ? el.scrollHeight > el.clientHeight + 1
        : el.scrollWidth > el.clientWidth + 1,
    );
  }, [text, wrap]);

  if (!text) {
    return (
      <Typography
        typography="s2"
        color="text.disabled"
        sx={{ fontStyle: "italic" }}
      >
        Empty
      </Typography>
    );
  }
  return (
    <Box
      sx={{
        position: "relative",
        minWidth: 0,
        width: "100%",
        "&:hover .cell-expand": { opacity: 1 },
      }}
    >
      <Box
        ref={ref}
        sx={{
          typography: "s2",
          lineHeight: "20px",
          color: "text.primary",
          fontFamily: json ? "monospace" : "inherit",
          fontSize: json ? 12 : undefined,
          ...(wrap
            ? {
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                maxHeight: WRAP_MAX_HEIGHT - 16,
                overflow: "hidden",
                ...(truncated && {
                  maskImage:
                    "linear-gradient(to bottom, black 75%, transparent)",
                }),
              }
            : {
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                pr: truncated ? 2.5 : 0,
              }),
        }}
      >
        {wrap ? text : text.replace(/\s+/g, " ")}
      </Box>
      {truncated && (
        <IconButton
          className="cell-expand"
          size="small"
          aria-label="Show full value"
          onClick={(e) => {
            e.stopPropagation();
            setAnchor(e.currentTarget);
          }}
          onDoubleClick={(e) => e.stopPropagation()}
          sx={{
            position: "absolute",
            right: -4,
            top: wrap ? "auto" : "50%",
            bottom: wrap ? 0 : "auto",
            transform: wrap ? "none" : "translateY(-50%)",
            p: 0.25,
            opacity: 0,
            bgcolor: "background.paper",
            border: 1,
            borderColor: "divider",
            "&:hover": { bgcolor: "background.neutral" },
          }}
        >
          <Iconify icon="mdi:arrow-expand" width={12} />
        </IconButton>
      )}
      <ValuePopover
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        title={title}
        value={value}
      />
    </Box>
  );
};

TextValue.propTypes = {
  value: PropTypes.any,
  wrap: PropTypes.bool,
  title: PropTypes.string,
  json: PropTypes.bool,
};

/**
 * Wraps a versioned-grid cell. `children` is the classic renderer, used for
 * non-text cells and in the Comfortable / Tall densities.
 */
const CellChrome = ({
  datasetId,
  rowId,
  columnId,
  columnName,
  value,
  cell,
  children,
}) => {
  const { density } = useDataGridView(datasetId);
  const pending = useChangeSetStore((s) =>
    (s.sets[datasetId]?.ops ?? []).find(
      (o) =>
        o.op === "update_cell" &&
        o.row_id === rowId &&
        o.column_id === columnId,
    ),
  );
  const textual = isTextualCell(cell);
  const compact = density === "compact";
  const wrap = density === "wrap";
  // R2 (P6, L6): structured / YAML / huge values keep their typed summary
  // in Compact density (and any column with a `View as` mode).
  const typed = useTypedCell({
    value,
    dataType: cell.dataType,
    columnId,
    enabled: textual && compact,
  });

  const content =
    textual && (compact || wrap) && !typed ? (
      <Box
        sx={{
          display: "flex",
          alignItems: wrap ? "flex-start" : "center",
          height: wrap ? "auto" : "100%",
          px: 1,
          py: wrap ? 1 : 0,
          minWidth: 0,
        }}
      >
        <TextValue
          value={value}
          wrap={wrap}
          title={columnName}
          json={cell.dataType === "json" || cell.dataType === "array"}
        />
      </Box>
    ) : (
      children
    );

  if (!pending) return content;
  const was = toText(pending.before);
  return (
    <CustomTooltip
      show
      title={`Edited · was: “${was ? clip(was) : "empty"}”`}
      placement="top"
      size="small"
      arrow
      enterDelay={300}
    >
      <Box
        sx={{
          position: "relative",
          height: wrap ? "auto" : "100%",
          bgcolor: (t) => alpha(t.palette.warning.main, 0.16),
          "&::after": {
            content: '""',
            position: "absolute",
            top: 0,
            right: 0,
            borderStyle: "solid",
            borderWidth: "0 6px 6px 0",
            borderColor: (t) =>
              `transparent ${t.palette.warning.main} transparent transparent`,
          },
        }}
      >
        {content}
      </Box>
    </CustomTooltip>
  );
};

CellChrome.propTypes = {
  datasetId: PropTypes.string,
  rowId: PropTypes.string,
  columnId: PropTypes.string,
  columnName: PropTypes.string,
  value: PropTypes.any,
  cell: PropTypes.shape({
    dataType: PropTypes.string,
    originType: PropTypes.string,
    status: PropTypes.string,
  }),
  children: PropTypes.node,
};

export default CellChrome;
