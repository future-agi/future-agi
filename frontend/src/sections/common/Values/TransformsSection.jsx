import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  TRANSFORM_KINDS,
  applyTransforms,
  transformLabel,
  transformsOf,
} from "./transforms";

/**
 * W3-4 `Transformations` (Schema drawer): per column, run when rows are
 * added (imports, paste, new rows). Existing rows change only through
 * `Apply to existing rows…`, which makes pending `update_cell` changes.
 */
export default function TransformsSection({
  columns,
  rows,
  valueOf,
  schema,
  saving,
  onSave,
  onApply,
}) {
  const list = transformsOf(schema);
  const [column, setColumn] = useState("");
  const [kind, setKind] = useState("");
  const blocked = !column
    ? "Pick a column"
    : !kind
      ? "Pick a transformation"
      : list.some((t) => t.column === column && t.kind === kind)
        ? "Already set on this column"
        : null;

  // Pending `update_cell` ops the transforms would make on loaded rows.
  const existingOps = useMemo(() => {
    if (!list.length) return [];
    const byName = new Map(columns.map((c) => [c.name, c.id]));
    const ops = [];
    (rows ?? []).forEach((r) => {
      const cells = Object.fromEntries(
        columns.map((c) => [c.name, valueOf(r, c.id)]),
      );
      const t = applyTransforms(cells, schema);
      t.applied.forEach(({ column: name }) => {
        const id = byName.get(name);
        if (!id || !r.row_id) return;
        if (ops.some((o) => o.row_id === r.row_id && o.column_id === id))
          return;
        ops.push({
          op: "update_cell",
          row_id: r.row_id,
          column_id: id,
          value: t.cells[name],
          before: cells[name],
        });
      });
    });
    return ops;
  }, [list.length, rows, columns, valueOf, schema]);

  return (
    <Stack
      gap={1}
      sx={{ mt: 2, pt: 2, borderTop: "1px solid", borderColor: "divider" }}
      data-testid="schema-transforms"
    >
      <Typography typography="s1" fontWeight="fontWeightSemiBold">
        Transformations
      </Typography>
      <Typography typography="s3" color="text.secondary">
        Run on every row you add: imports, paste and new rows. Existing rows
        change only when you apply them.
      </Typography>
      {list.map((t) => (
        <Stack
          key={`${t.column}-${t.kind}`}
          direction="row"
          alignItems="center"
          gap={1}
          data-testid="schema-transform-row"
        >
          <Typography typography="s2" sx={{ fontFamily: "monospace" }}>
            {t.column}
          </Typography>
          <Typography typography="s2" color="text.secondary">
            {transformLabel(t.kind)}
          </Typography>
          <Box sx={{ flex: 1 }} />
          <IconButton
            size="small"
            aria-label={`Remove ${transformLabel(t.kind)} on ${t.column}`}
            disabled={saving}
            onClick={() =>
              onSave(
                list.filter(
                  (x) => !(x.column === t.column && x.kind === t.kind),
                ),
              )
            }
          >
            <Iconify icon="mdi:close" width={16} />
          </IconButton>
        </Stack>
      ))}
      <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
        <Select
          size="small"
          displayEmpty
          value={column}
          onChange={(e) => setColumn(e.target.value)}
          inputProps={{ "aria-label": "Transformation column" }}
          sx={{ minWidth: 150, typography: "s2" }}
        >
          <MenuItem value="" disabled>
            Column
          </MenuItem>
          {columns.map((c) => (
            <MenuItem key={c.id} value={c.name} sx={{ typography: "s2" }}>
              {c.name}
            </MenuItem>
          ))}
        </Select>
        <Select
          size="small"
          displayEmpty
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          inputProps={{ "aria-label": "Transformation" }}
          sx={{ minWidth: 220, typography: "s2" }}
        >
          <MenuItem value="" disabled>
            Transformation
          </MenuItem>
          {TRANSFORM_KINDS.map((t) => (
            <MenuItem key={t.kind} value={t.kind} sx={{ typography: "s2" }}>
              {t.label}
            </MenuItem>
          ))}
        </Select>
        <CustomTooltip
          show={Boolean(blocked)}
          title={blocked ?? ""}
          size="small"
        >
          <Box component="span">
            <Button
              size="small"
              variant="outlined"
              disabled={Boolean(blocked) || saving}
              onClick={() => {
                onSave([...list, { column, kind }]);
                setKind("");
              }}
              data-testid="schema-transform-add"
            >
              Add
            </Button>
          </Box>
        </CustomTooltip>
      </Stack>
      {list.length > 0 && (
        <CustomTooltip
          show={!existingOps.length}
          title="No loaded row changes"
          size="small"
        >
          <Box component="span" sx={{ alignSelf: "flex-start" }}>
            <Button
              size="small"
              variant="text"
              disabled={!existingOps.length}
              onClick={() => onApply(existingOps)}
              data-testid="schema-transform-apply"
            >
              {`Apply to existing rows… (${existingOps.length} ${existingOps.length === 1 ? "cell" : "cells"})`}
            </Button>
          </Box>
        </CustomTooltip>
      )}
    </Stack>
  );
}

TransformsSection.propTypes = {
  columns: PropTypes.array.isRequired,
  rows: PropTypes.array,
  valueOf: PropTypes.func.isRequired,
  schema: PropTypes.object,
  saving: PropTypes.bool,
  onSave: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
};
