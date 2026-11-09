// W5-10 (DM-11; W5-SCOPE §7 C7): the Data grid line "Same on every row:
// system" with ⋯ Show values. It reads up to PREVIEW_SAMPLE_ROWS rows of the
// grid's current view (same filters as the grid) and names the user columns
// that hold one value on every one of them. Nothing is hidden; the line is
// absent when no column is constant.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { Box, IconButton, Popover, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import {
  getDataGridFilters,
  useDataGridView,
} from "src/sections/develop-detail/DataTab/DataGrid/useDataGridView";
import { isUserColumn } from "src/sections/develop-detail/DataTab/DataGrid/dataGridView";
import {
  PREVIEW_SAMPLE_ROWS,
  chooseField,
  jsonCellValue,
} from "./distinguishingField";

const show = (v) =>
  typeof v === "string" ? v : v == null ? "" : JSON.stringify(v, null, 1);

export default function SameOnEveryRowLine({ datasetId, columns }) {
  // Re-render when the grid's view (system filters) changes.
  useDataGridView(datasetId);
  const filters = getDataGridFilters(datasetId);
  const base = getDatasetQueryOptions(datasetId, 0, filters, [], "", {
    pageSize: PREVIEW_SAMPLE_ROWS,
  });
  const { data } = useQuery({
    ...base,
    // Starts with "dataset-detail" so a save's grid refresh reads it again.
    queryKey: [
      "dataset-detail",
      datasetId,
      "preview-sample",
      PREVIEW_SAMPLE_ROWS,
      filters,
    ],
    enabled: Boolean(datasetId),
    staleTime: 5000,
  });
  const result = data?.data?.result;
  const fields = useMemo(
    () =>
      (columns ?? [])
        .filter(isUserColumn)
        .map((c) => ({ key: c.id, label: c.name ?? c.id })),
    [columns],
  );
  // JSON columns arrive as text: read them as values, so an all-`[]`
  // column is empty, never "the same on every row" (M7).
  const jsonKeys = useMemo(
    () =>
      new Set(
        (columns ?? []).filter((c) => c?.data_type === "json").map((c) => c.id),
      ),
    [columns],
  );
  const choice = useMemo(
    () =>
      chooseField(result?.table ?? [], fields, {
        read: (row, key) =>
          jsonKeys.has(key) ? jsonCellValue(row?.[key]) : row?.[key],
      }),
    [result?.table, fields, jsonKeys],
  );
  const [anchor, setAnchor] = useState(null);
  if (!choice.same.length) return null;
  const total = result?.metadata?.total_rows ?? choice.sampled;
  const names = choice.same.map((s) => s.label).join(", ");
  return (
    <Box
      data-testid="same-on-every-row"
      sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.5 }}
    >
      <Typography variant="caption" color="text.secondary">
        Same on every row
        {total > choice.sampled
          ? ` (first ${choice.sampled.toLocaleString()} of ${total.toLocaleString()})`
          : ""}
        :{" "}
        <Box component="span" sx={{ color: "text.primary", fontWeight: 500 }}>
          {names}
        </Box>
      </Typography>
      <IconButton
        size="small"
        aria-label="Show values"
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{ p: 0.25 }}
      >
        <Iconify icon="mdi:dots-horizontal" width={16} />
      </IconButton>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      >
        <Stack
          data-testid="same-on-every-row-values"
          spacing={1}
          sx={{ p: 1.5, maxWidth: 520, maxHeight: 360, overflow: "auto" }}
        >
          <Typography variant="subtitle2">Show values</Typography>
          {choice.same.map((s) => (
            <Box key={s.key}>
              <Typography variant="caption" color="text.secondary">
                {s.label} · the same on all {choice.sampled.toLocaleString()}{" "}
                rows read
              </Typography>
              <Typography
                variant="body2"
                sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
              >
                {show(s.value)}
              </Typography>
            </Box>
          ))}
        </Stack>
      </Popover>
    </Box>
  );
}

SameOnEveryRowLine.propTypes = {
  datasetId: PropTypes.string,
  columns: PropTypes.array,
};
