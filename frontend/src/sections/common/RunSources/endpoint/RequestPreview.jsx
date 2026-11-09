// "Row [12 ▾] sends" (W6 B2): the request the run's try 1 sends for one
// row, rendered by the one builder as the template or the row changes. The
// row picked here is the row Test on one row sends. Secret headers show by
// reference; empty variables are counted over the loaded rows.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { MenuItem, Select, Stack, Typography } from "@mui/material";
import ScriptBlock from "../ScriptBlock";
import { emptyLine, emptyVariables, previewOf } from "./previewModel";

const labelOfDefault = (r) => `Row ${(r?.order ?? 0) + 1}`;

export default function RequestPreview({
  http,
  mapping,
  columns,
  rows,
  row,
  onRow,
  total,
  labelOf = labelOfDefault,
}) {
  const p = useMemo(
    () => previewOf({ http, mapping, columns, row }),
    [http, mapping, columns, row],
  );
  const gaps = useMemo(
    () => emptyVariables({ http, mapping, columns, rows }),
    [http, mapping, columns, rows],
  );
  if (!String(http?.url ?? "").trim() || !row) return null;
  const list = rows ?? [];
  const headers = Object.entries(p.shownHeaders);
  return (
    <Stack
      spacing={0.5}
      data-testid="http-request-preview"
      data-row={row.row_id ?? ""}
      data-body={p.body}
    >
      <Stack direction="row" gap={1} alignItems="center">
        <Select
          size="small"
          value={row.row_id ?? ""}
          onChange={(e) => onRow?.(e.target.value)}
          sx={{ height: 28, typography: "s2", minWidth: 110 }}
          inputProps={{
            "aria-label": "Preview row",
            "data-testid": "http-preview-row",
          }}
        >
          {list.map((r) => (
            <MenuItem key={r.row_id} value={r.row_id} sx={{ typography: "s2" }}>
              {labelOf(r)}
            </MenuItem>
          ))}
        </Select>
        <Typography typography="s2" fontWeight="fontWeightMedium">
          sends
        </Typography>
        <Typography
          typography="s2"
          sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
          data-testid="http-preview-line"
        >
          {`${p.method} ${p.url}`}
        </Typography>
      </Stack>
      {headers.length ? (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ fontFamily: "monospace", wordBreak: "break-word" }}
          data-testid="http-preview-headers"
        >
          {headers.map(([k, v]) => `${k}: ${v}`).join(" · ")}
        </Typography>
      ) : null}
      {p.method !== "GET" ? (
        <ScriptBlock
          text={p.body || " "}
          language="json"
          dense
          copyTooltip="Copy body"
          testId="http-preview-body"
        />
      ) : null}
      {gaps.map((v) => (
        <Typography
          key={`${v.kind}-${v.name}`}
          typography="s3"
          color="warning.dark"
          data-testid="http-preview-empty"
        >
          {`${emptyLine(v, list.length, total)} · ${v.rows
            .slice(0, 8)
            .map(labelOf)
            .join(", ")}${v.rows.length > 8 ? ", …" : ""}`}
        </Typography>
      ))}
    </Stack>
  );
}

RequestPreview.propTypes = {
  http: PropTypes.object,
  mapping: PropTypes.object,
  columns: PropTypes.arrayOf(PropTypes.object),
  rows: PropTypes.arrayOf(PropTypes.object),
  row: PropTypes.object,
  onRow: PropTypes.func,
  total: PropTypes.number,
  labelOf: PropTypes.func,
};
