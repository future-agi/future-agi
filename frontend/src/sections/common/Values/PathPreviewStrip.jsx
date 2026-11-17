import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { exampleText } from "./inferSchema";
import { formatPath, parsePath } from "./faPath";

/**
 * `{colId, rel}` of an eval mapping value (`<column id>.<path>` or
 * `<column id>[0]…`) among the columns, or null for a plain column.
 */
export function splitEvalMapping(value, columns = []) {
  const v = String(value ?? "");
  const col = columns
    .map((c) => c?.field ?? c?.id)
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)
    .find((id) => v.startsWith(id) && v.length > id.length);
  if (!col) return null;
  const rest = v.slice(col.length).replace(/^\./, "");
  const parsed = parsePath(rest.startsWith("[") ? rest : `$.${rest}`);
  if (!parsed.ok) return null;
  return { colId: col, rel: formatPath(null, parsed.steps) };
}

/**
 * PathPreviewStrip — one line under a mapped field (R2-SPEC P6 §3.3):
 * `Example: "enterprise" · in 34 of 40 rows` (amber under 90 %), from the
 * json-schema's typed `paths[]`. Renders nothing for plain columns.
 */
const PathPreviewStrip = ({ value, jsonSchemas, columns }) => {
  const hit = splitEvalMapping(value, columns);
  if (!hit) return null;
  const entry = jsonSchemas?.[hit.colId]?.paths?.find((p) => p.rel === hit.rel);
  if (!entry) return null;
  const { present, total } = entry.coverage;
  const low = total && present / total < 0.9;
  const where =
    present === total
      ? `in all ${total.toLocaleString("en-US")} rows`
      : `in ${present.toLocaleString("en-US")} of ${total.toLocaleString("en-US")} rows`;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{ mt: 0.5, minWidth: 0 }}
    >
      {low && (
        <Iconify
          icon="mdi:alert-outline"
          width={14}
          sx={{ color: "amber.500", flexShrink: 0 }}
        />
      )}
      <Typography
        typography="s3"
        noWrap
        sx={{ color: low ? "amber.700" : "text.secondary", minWidth: 0 }}
      >
        {entry.example !== null && entry.example !== undefined
          ? `Example: ${exampleText(entry.example)} · ${where}`
          : `Found ${where}`}
      </Typography>
    </Stack>
  );
};

PathPreviewStrip.propTypes = {
  value: PropTypes.any,
  jsonSchemas: PropTypes.object,
  columns: PropTypes.array,
};

export default PathPreviewStrip;
