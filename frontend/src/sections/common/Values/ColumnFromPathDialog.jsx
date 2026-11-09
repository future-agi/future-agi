import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { useGetJsonColumnSchema } from "src/api/develop/develop-detail";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import PathInput from "./PathInput";
import { formatPath, parsePath } from "./faPath";
import { kindIcon } from "./kindIcons";
import { useColumnFromPathStore } from "./columnFromPathStore";
import { MONO } from "./CodeBlock";
import { revealGridColumn } from "./revealColumn";
import { previewSummary } from "./columnFromPathPreview";
import { notify } from "src/sections/common/ux/toast";
import CustomTooltip from "src/components/tooltip";

const TYPE_LABEL = {
  integer: "Number",
  float: "Number",
  boolean: "Boolean",
  json: "JSON",
  text: "Text",
};
const TYPE_KIND = {
  integer: "number",
  float: "number",
  boolean: "boolean",
  json: "object",
  text: "text",
};

/** `{column_id, json_key}` for a full path typed in the dialog. */
export function extractBody(columnId, columnName, path) {
  const parsed = parsePath(path, { columns: [columnName] });
  if (!parsed.ok || parsed.column !== columnName || !parsed.steps.length)
    return null;
  return { column_id: columnId, json_key: formatPath(null, parsed.steps) };
}

/**
 * Column from path (R2-SPEC P6 §3.3): a new column from an FA Path in a
 * json column — live preview of the first 5 rows, `Found in 31 of 40
 * rows`, the inferred type, then `Add column` (the existing
 * extract-json-column endpoint). Opened from the column menu
 * (columnMenuItems → columnFromPathStore); mounted by SchemaDrawer.
 */
const ColumnFromPathDialog = ({ datasetId: fallbackDatasetId }) => {
  const target = useColumnFromPathStore((s) => s.target);
  const close = useColumnFromPathStore((s) => s.close);
  const datasetId = target?.datasetId ?? fallbackDatasetId;
  const open = Boolean(target);
  const { refreshGrid, gridApi } = useDevelopDetailContext();
  const queryClient = useQueryClient();
  const { data: schemas } = useGetJsonColumnSchema(datasetId, {
    enabled: Boolean(open && datasetId),
  });
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [debounced, setDebounced] = useState("");

  useEffect(() => {
    if (!target) return;
    setPath(target.path ?? (target.columnName ? `${target.columnName}.` : ""));
    setName("");
    setNameTouched(false);
    setDebounced("");
  }, [target]);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(path), 250);
    return () => clearTimeout(t);
  }, [path]);

  // IT-R1 G5 P8: opened without a column (`+ Column`, View › Columns), the
  // path names the json column itself (`metadata.customer.tier`).
  const jsonColumns = useMemo(
    () =>
      Object.entries(schemas ?? {})
        .map(([id, e]) => ({ id, name: e?.name ?? e?.column_name ?? id }))
        .filter((c) => c.name),
    [schemas],
  );
  const fixed = Boolean(target?.columnId);
  const columnNames = fixed
    ? [target.columnName]
    : jsonColumns.map((c) => c.name);
  const parsedTyped = useMemo(
    () =>
      debounced.trim() ? parsePath(debounced, { columns: columnNames }) : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [debounced, columnNames.join("|")],
  );
  const column = fixed
    ? { id: target.columnId, name: target.columnName }
    : jsonColumns.find((c) => c.name === parsedTyped?.column) ?? null;
  const body = useMemo(
    () =>
      target && column ? extractBody(column.id, column.name, debounced) : null,
    [target, column?.id, column?.name, debounced], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const columnSchemas = useMemo(() => {
    if (!schemas || !target) return {};
    if (!fixed) return schemas;
    const entry = schemas[target.columnId];
    return entry ? { [target.columnId]: entry } : {};
  }, [schemas, target, fixed]);

  const preview = useMutation({
    mutationFn: (b) =>
      axios.post(
        endpoints.develop.addColumns.preview(datasetId, "extract_json"),
        b,
      ),
  });
  const { mutate: runPreview, reset: resetPreview } = preview;
  useEffect(() => {
    // L2: dev's preview route is strict (PreviewDatasetOperationRequest has
    // no new_column_name; the name travels only with Add column).
    if (body)
      runPreview({ column_id: body.column_id, json_key: body.json_key });
    else resetPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [body?.column_id, body?.json_key]);

  const result = preview.data?.data?.result;
  const summary = previewSummary(result);
  const add = useMutation({
    mutationFn: (b) =>
      axios.post(endpoints.develop.addColumns.extractJsonKey(datasetId), b),
    onSuccess: (res, b) => {
      const colId = res?.data?.result?.column_id;
      // IT-R1 G5 P8: the new column lands at the far right; scroll to it
      // and flash it, and the toast can bring it back.
      const show = () => colId && revealGridColumn(gridApi, colId);
      notify(`Added ${b.new_column_name ?? b.json_key ?? "column"}`, {
        variant: "success",
        key: "column-from-path",
        action: colId
          ? () => (
              <Button
                size="small"
                color="inherit"
                onClick={show}
                sx={{ fontWeight: "fontWeightSemiBold" }}
              >
                Show
              </Button>
            )
          : undefined,
      });
      queryClient.invalidateQueries({
        queryKey: ["json-column-schema", datasetId],
      });
      refreshGrid?.(null, true);
      close();
      show();
    },
  });

  const finalName = (nameTouched ? name : path).trim();
  const typeLabel = summary?.dataType ? TYPE_LABEL[summary.dataType] : null;
  const parsedNow = target ? parsePath(path, { columns: columnNames }) : null;
  const outside =
    fixed && parsedNow?.ok && parsedNow.column !== target?.columnName
      ? `Start the path with ${target?.columnName}`
      : null;
  const addReason =
    !path.trim() || !body
      ? "Pick a path first"
      : outside
        ? outside
        : !result
          ? "Checking rows…"
          : !finalName
            ? "Name the column"
            : null;

  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="sm">
      <DialogTitle
        sx={{ display: "flex", alignItems: "center", gap: 1, pb: 1 }}
      >
        <Typography
          typography="m3"
          fontWeight="fontWeightSemiBold"
          sx={{ flex: 1 }}
        >
          Column from path
        </Typography>
        <IconButton size="small" aria-label="Close" onClick={close}>
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          pt: "8px !important",
        }}
      >
        <Stack gap={0.75}>
          <Typography typography="s2" color="text.secondary">
            {fixed ? (
              <>
                Path in{" "}
                <Box
                  component="span"
                  sx={{ fontFamily: MONO, color: "text.primary" }}
                >
                  {target?.columnName}
                </Box>
              </>
            ) : (
              `Path in a JSON column: ${jsonColumns.map((c) => c.name).join(", ") || "none yet"}`
            )}
          </Typography>
          <PathInput
            autoFocus
            value={path}
            onChange={(v) => setPath(v)}
            columns={target ? columnNames : []}
            schemas={columnSchemas}
            error={outside ?? undefined}
            onEnter={() => {}}
          />
        </Stack>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={1}
            sx={{ px: 1.5, py: 0.75, bgcolor: "background.neutral" }}
          >
            <Typography
              typography="s2"
              fontWeight="fontWeightMedium"
              sx={{ flex: 1 }}
            >
              {summary
                ? summary.label
                : body
                  ? "Checking rows…"
                  : "Type a path to preview it"}
            </Typography>
            {typeLabel && summary.found > 0 && (
              <Stack
                direction="row"
                alignItems="center"
                gap={0.5}
                sx={{ px: 0.75, borderRadius: 0.5, bgcolor: "action.hover" }}
              >
                <Iconify
                  icon={kindIcon(TYPE_KIND[summary.dataType])}
                  width={14}
                />
                <Typography typography="s3" fontWeight={500}>
                  {typeLabel}
                </Typography>
              </Stack>
            )}
          </Stack>
          {(summary?.rows ?? []).map((r, i) => (
            <Stack
              key={r.row_id}
              direction="row"
              gap={1.5}
              sx={{
                px: 1.5,
                py: 0.5,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ width: 48, flexShrink: 0 }}
              >
                {`Row ${i + 1}`}
              </Typography>
              <Typography
                typography="s2"
                noWrap
                sx={{
                  fontFamily: MONO,
                  color: r.found ? "text.primary" : "text.disabled",
                  minWidth: 0,
                }}
              >
                {r.found
                  ? `→ ${typeof r.output === "object" ? JSON.stringify(r.output) : r.output}`
                  : "→ not found"}
              </Typography>
            </Stack>
          ))}
        </Box>
        <TextField
          label="Column name"
          size="small"
          value={nameTouched ? name : path}
          onChange={(e) => {
            setNameTouched(true);
            setName(e.target.value);
          }}
          inputProps={{ spellCheck: false }}
        />
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button variant="outlined" color="inherit" onClick={close}>
          Cancel
        </Button>
        <CustomTooltip show={Boolean(addReason)} title={addReason} size="small">
          <span>
            <LoadingButton
              variant="contained"
              color="primary"
              loading={add.isPending}
              disabled={Boolean(addReason)}
              onClick={() =>
                add.mutate({
                  ...body,
                  new_column_name: finalName,
                  concurrency: 1,
                })
              }
            >
              Add column
            </LoadingButton>
          </span>
        </CustomTooltip>
      </DialogActions>
    </Dialog>
  );
};

ColumnFromPathDialog.propTypes = { datasetId: PropTypes.string };

export default ColumnFromPathDialog;
