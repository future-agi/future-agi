import React, { useId, useMemo, useState } from "react";
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
import NotInReleaseChip from "src/components/NotInRelease/NotInReleaseChip";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { isRealBackend } from "src/sections/develop/realBackendCaps";
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
 *
 * On the real backend the schema save keeps only the declared schema, so a
 * transformation is never stored: the part stays on screen, turned off, with
 * the shared line beside its title, and every control is disabled and
 * described by that line.
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
  const held = isRealBackend();
  const reasonId = useId();
  const describedBy = held ? reasonId : undefined;
  const blocked = held
    ? NOT_IN_RELEASE
    : !column
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
      <Stack direction="row" alignItems="center" gap={1}>
        <Typography typography="s1" fontWeight="fontWeightSemiBold">
          Transformations
        </Typography>
        {held && (
          <Box component="span" id={reasonId} sx={{ display: "inline-flex" }}>
            <NotInReleaseChip />
          </Box>
        )}
      </Stack>
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
            aria-describedby={describedBy}
            disabled={held || saving}
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
          disabled={held}
          inputProps={{
            "aria-label": "Transformation column",
            "aria-describedby": describedBy,
          }}
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
          disabled={held}
          inputProps={{
            "aria-label": "Transformation",
            "aria-describedby": describedBy,
          }}
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
              aria-describedby={describedBy}
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
          show={held || !existingOps.length}
          title={held ? NOT_IN_RELEASE : "No loaded row changes"}
          size="small"
        >
          <Box component="span" sx={{ alignSelf: "flex-start" }}>
            <Button
              size="small"
              variant="text"
              disabled={held || !existingOps.length}
              aria-describedby={describedBy}
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
