// JsonFieldsDiff (R4 lane M): JSON outputs field by field. Columns: field ·
// expected · one per lane (baseline first). Each lane cell is ✓ / ✗ against
// the expected field (word diff on strings, `+12` on numbers); without
// expected fields the lanes are diffed against the baseline lane and no
// verdict is shown. `Raw JSON` swaps the table for the reused JsonDiffView
// (baseline → each other lane). The field path is in each row's hover.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import JsonDiffView from "src/sections/common/Values/JsonDiffView";
import { NUM_SX } from "src/sections/common/format/fmt";
import LaneLabel from "./LaneLabel";

const text = (v) =>
  v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v);

const signed = (n) =>
  `${n > 0 ? "+" : n < 0 ? "−" : "±"}${Math.abs(n).toLocaleString("en-US")}`;

function Cell({ cell, against }) {
  if (!cell.present)
    return (
      <Stack direction="row" gap={0.5} alignItems="center">
        {cell.match === false ? (
          <Iconify icon="mdi:close" width={14} sx={{ color: "error.main" }} />
        ) : null}
        <Typography typography="s2" color="text.disabled">
          Missing
        </Typography>
      </Stack>
    );
  // Against expected: ✓ / ✗. Against the baseline: no mark when the same,
  // `≠` (amber) when it differs, never a verdict.
  const mark =
    against !== "expected" ? (
      cell.match === false ? (
        <Iconify
          icon="mdi:not-equal-variant"
          width={14}
          sx={{ color: "warning.dark", flexShrink: 0, mt: 0.25 }}
          aria-label="Differs from the baseline"
        />
      ) : null
    ) : cell.match === true ? (
      <Iconify
        icon="mdi:check"
        width={14}
        sx={{ color: "success.main", flexShrink: 0, mt: 0.25 }}
        aria-label="Matches"
      />
    ) : cell.match === false ? (
      <Iconify
        icon="mdi:close"
        width={14}
        sx={{ color: "error.main", flexShrink: 0, mt: 0.25 }}
        aria-label="Does not match"
      />
    ) : null;
  const body =
    cell.match === false &&
    typeof cell.value === "string" &&
    typeof cell.vs === "string" ? (
      <TextDiff from={cell.vs} to={cell.value} />
    ) : (
      <Typography
        component="span"
        typography="s2"
        sx={typeof cell.value === "number" ? NUM_SX : undefined}
      >
        {text(cell.value)}
        {cell.numDelta != null ? (
          <Typography
            component="span"
            typography="s3"
            color={against === "expected" ? "error.main" : "warning.dark"}
            sx={{ ml: 0.5, ...NUM_SX }}
          >
            {signed(cell.numDelta)}
          </Typography>
        ) : null}
      </Typography>
    );
  return (
    <Stack
      direction="row"
      gap={0.5}
      alignItems="flex-start"
      sx={{ minWidth: 0, wordBreak: "break-word" }}
    >
      {mark}
      <Box sx={{ minWidth: 0 }}>{body}</Box>
    </Stack>
  );
}

Cell.propTypes = {
  cell: PropTypes.object.isRequired,
  against: PropTypes.string,
};

export default function JsonFieldsDiff({ table, lanes, raw, onRawChange }) {
  const [rawLocal, setRawLocal] = useState(false);
  const showRaw = raw ?? rawLocal;
  const setRaw = onRawChange ?? setRawLocal;
  const baseIdx = Math.max(
    0,
    lanes.findIndex((l) => l.isBaseline),
  );
  const withExpected = table.against === "expected";
  const cols = `minmax(96px, 0.8fr)${withExpected ? " minmax(80px, 1fr)" : ""}${" minmax(96px, 1fr)".repeat(lanes.length)}`;
  return (
    <Stack
      gap={1}
      sx={{ minWidth: 0 }}
      data-json-fields-diff={table.against ?? "none"}
    >
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography typography="s3" color="text.secondary" sx={{ flex: 1 }}>
          {withExpected
            ? table.source === "eval"
              ? "Each field against the Field match eval's expected value"
              : "Each field against the expected value"
            : "No expected fields on this row · each run is compared with the baseline"}
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={showRaw ? "raw" : "fields"}
          onChange={(_, v) => v && setRaw(v === "raw")}
          aria-label="Field table or raw JSON"
          sx={{
            "& .MuiToggleButton-root": {
              height: 24,
              px: 1,
              typography: "s3",
              textTransform: "none",
            },
          }}
        >
          <ToggleButton value="fields">Fields</ToggleButton>
          <ToggleButton value="raw">Raw JSON</ToggleButton>
        </ToggleButtonGroup>
      </Stack>
      {showRaw ? (
        <Stack gap={1.5}>
          {lanes.map((l, i) =>
            i === baseIdx ? null : (
              <Stack key={l.runId} gap={0.5}>
                <Stack direction="row" gap={0.5} alignItems="center">
                  <LaneLabel lane={lanes[baseIdx]} dense />
                  <Iconify icon="mdi:arrow-right" width={14} />
                  <LaneLabel lane={l} dense />
                </Stack>
                <JsonDiffView from={lanes[baseIdx].value} to={l.value} />
              </Stack>
            ),
          )}
        </Stack>
      ) : (
        <Box
          role="table"
          aria-label="Fields by run"
          sx={{
            display: "grid",
            gridTemplateColumns: cols,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
            "& > [role=row]": { display: "contents" },
            "& [role=cell], & [role=columnheader]": {
              px: 1,
              py: 0.75,
              borderTop: "1px solid",
              borderColor: "divider",
              minWidth: 0,
            },
            "& [role=columnheader]": {
              borderTop: 0,
              bgcolor: "background.neutral",
              position: "sticky",
              top: 0,
              zIndex: 1,
            },
          }}
        >
          <Box role="row">
            <Box role="columnheader">
              <Typography typography="s3" color="text.secondary">
                Field
              </Typography>
            </Box>
            {withExpected ? (
              <Box role="columnheader">
                <Typography typography="s3" color="text.secondary">
                  Expected
                </Typography>
              </Box>
            ) : null}
            {lanes.map((l) => (
              <Box role="columnheader" key={l.runId}>
                <LaneLabel lane={l} dense />
              </Box>
            ))}
          </Box>
          {table.rows.map((r) => (
            <Box role="row" key={r.key} data-field-row={r.key}>
              <Box role="cell">
                <CustomTooltip
                  show
                  size="small"
                  placement="top-start"
                  title={`Path: output.${r.key}`}
                >
                  <Typography
                    typography="s2"
                    sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
                  >
                    {r.key}
                  </Typography>
                </CustomTooltip>
              </Box>
              {withExpected ? (
                <Box role="cell">
                  <Typography
                    typography="s2"
                    color={r.hasExpected ? "text.primary" : "text.disabled"}
                    sx={{ wordBreak: "break-word" }}
                  >
                    {r.hasExpected ? text(r.expected) : "Not checked"}
                  </Typography>
                </Box>
              ) : null}
              {r.cells.map((c, i) => (
                <Box role="cell" key={lanes[i].runId}>
                  <Cell cell={c} against={table.against} />
                </Box>
              ))}
            </Box>
          ))}
        </Box>
      )}
    </Stack>
  );
}

JsonFieldsDiff.propTypes = {
  table: PropTypes.object.isRequired,
  lanes: PropTypes.array.isRequired,
  raw: PropTypes.bool,
  onRawChange: PropTypes.func,
};
