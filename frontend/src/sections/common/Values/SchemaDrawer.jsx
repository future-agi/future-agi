import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  Drawer,
  FormControlLabel,
  IconButton,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { parse as parseYaml, stringify as yamlStringify } from "yaml";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import axios from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { apiPath } from "src/api/contracts/api-surface";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { useGetDatasetDetail } from "src/api/develop/develop-detail";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import SchemaMismatchRows from "./SchemaMismatchRows";
import ColumnFromPathDialog from "./ColumnFromPathDialog";
import {
  coverageText,
  exampleText,
  inferColumns,
  inferPaths,
  kindsMixText,
} from "./inferSchema";
import { SCHEMA_PRESETS, presetById, schemaFromInferred } from "./presets";
import {
  enforceModeOf,
  groupIssues,
  schemaProblems,
  validateRow,
} from "./schemaValidate";
import { MONO } from "./CodeBlock";
import TransformsSection from "./TransformsSection";
import { useChangeSet } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { kindIcon } from "./kindIcons";

const WIDTH = 560;
const TABLE_PARAMS = { page_size: 500, current_page_index: 0 };
const JSON_TYPES = new Set([
  "json",
  "conversation",
  "checks",
  "trace",
  "array",
  "images",
]);
const NUMBER_TYPES = new Set(["float", "integer"]);
const SKIP_ORIGINS = new Set(["evaluation", "evaluation_reason"]);
// W3-2: a schema covers the user's data only (the same columns the server
// checks): generated outputs, evals and human labels are left out.
const USER_ROLES = new Set(["input", "expected", "metadata"]);
const isUserColumn = (c) =>
  c?.role
    ? USER_ROLES.has(String(c.role).toLowerCase())
    : !SKIP_ORIGINS.has(c?.origin_type);
const KIND_ORDER = ["string", "number", "boolean", "object", "array", "null"];
const KIND_COLOR = {
  string: "blue.500",
  number: "green.500",
  boolean: "purple.500",
  object: "grey.600",
  array: "grey.500",
  null: "grey.300",
};

const cellValue = (row, id) => {
  const c = row?.[id];
  return c && typeof c === "object" ? c.cell_value : c;
};

const KindsBar = ({ kinds }) => (
  <CustomTooltip
    show
    size="small"
    placement="top"
    title={kindsMixText(kinds) || "No values"}
  >
    <Box
      sx={{
        display: "flex",
        width: 72,
        height: 8,
        borderRadius: 4,
        overflow: "hidden",
        bgcolor: "action.hover",
        flexShrink: 0,
      }}
    >
      {KIND_ORDER.filter((k) => kinds?.[k] > 0).map((k) => (
        <Box
          key={k}
          sx={{ width: `${kinds[k] * 100}%`, bgcolor: KIND_COLOR[k] }}
        />
      ))}
    </Box>
  </CustomTooltip>
);

KindsBar.propTypes = { kinds: PropTypes.object };

const valuesText = (e) => {
  if (e.enum_values?.length)
    return e.enum_values.length > 3
      ? `${e.enum_values.slice(0, 3).join(", ")} +${e.enum_values.length - 3}`
      : e.enum_values.join(", ");
  if (e.min !== undefined)
    return e.min === e.max
      ? String(e.min)
      : `${e.min.toLocaleString("en-US")}–${e.max.toLocaleString("en-US")}`;
  if (e.array_max) return `≤ ${e.array_max.toLocaleString("en-US")} items`;
  if (e.max_length) return `≤ ${e.max_length.toLocaleString("en-US")} chars`;
  return "—";
};

const mainKind = (kinds = {}) =>
  KIND_ORDER.filter((k) => k !== "null").sort(
    (a, b) => (kinds[b] ?? 0) - (kinds[a] ?? 0),
  )[0];

const InferredRow = ({ entry, depth = 0, expandable, open, onToggle }) => (
  <Box
    component="tr"
    onClick={expandable ? onToggle : undefined}
    sx={{
      cursor: expandable ? "pointer" : "default",
      "& td": {
        py: 0.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        verticalAlign: "middle",
        typography: "s2",
      },
      "&:hover td": { bgcolor: "action.hover" },
    }}
  >
    <Box component="td" sx={{ pl: 1 + depth * 2, pr: 1, maxWidth: 180 }}>
      <Stack direction="row" alignItems="center" gap={0.5} sx={{ minWidth: 0 }}>
        <Box sx={{ width: 14, flexShrink: 0, display: "flex" }}>
          {expandable && (
            <Iconify
              icon={open ? "eva:chevron-down-fill" : "eva:chevron-right-fill"}
              width={14}
            />
          )}
        </Box>
        <Iconify
          icon={kindIcon(mainKind(entry.kinds))}
          width={14}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography
          typography="s2"
          noWrap
          sx={{ fontFamily: MONO, minWidth: 0 }}
          title={entry.path}
        >
          {entry.path}
        </Typography>
      </Stack>
    </Box>
    <Box component="td" sx={{ pr: 1 }}>
      <Stack gap={0.25}>
        <KindsBar kinds={entry.kinds} />
        <Typography typography="s3" color="text.secondary" noWrap>
          {kindsMixText(entry.kinds)}
        </Typography>
      </Stack>
    </Box>
    <Box
      component="td"
      sx={{ pr: 1, whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
    >
      {coverageText(entry.coverage)}
    </Box>
    <Box component="td" sx={{ pr: 1, maxWidth: 110 }}>
      <Typography
        typography="s3"
        noWrap
        sx={{ fontFamily: MONO, color: "text.secondary" }}
        title={exampleText(entry.example)}
      >
        {entry.example === null || entry.example === undefined
          ? "—"
          : exampleText(entry.example)}
      </Typography>
    </Box>
    <Box component="td" sx={{ pr: 1, maxWidth: 120 }}>
      <Typography
        typography="s3"
        noWrap
        color="text.secondary"
        title={valuesText(entry)}
      >
        {valuesText(entry)}
      </Typography>
    </Box>
  </Box>
);

InferredRow.propTypes = {
  entry: PropTypes.object.isRequired,
  depth: PropTypes.number,
  expandable: PropTypes.bool,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
};

function downloadJson(name, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * The dataset schema (R2-SPEC P6 §3.4), from the Data toolbar: inferred
 * types per column and JSON path (kind mix, coverage, example, values),
 * presets, a declared JSON Schema (Monaco, JSON | YAML) and `Check every
 * row against the schema` (stored in `dataset_config.schema` through
 * edit_dataset_behavior). Failing rows get the row-number glyph and the
 * `Invalid rows only` pill. Also mounts ColumnFromPathDialog.
 *
 * Props (frozen, R2 manifest row 30):
 * @param {boolean} props.open
 * @param {Function} props.onClose
 * @param {string} props.datasetId
 */
const SchemaDrawer = ({ open, onClose, datasetId }) => (
  <>
    {open && <SchemaDrawerBody onClose={onClose} datasetId={datasetId} />}
    <ColumnFromPathDialog datasetId={datasetId} />
  </>
);

const SchemaDrawerBody = ({ onClose, datasetId }) => {
  const queryClient = useQueryClient();
  const { refreshGrid } = useDevelopDetailContext();
  const [inspectedRowIds, setInspectedRowIds] = useState(null);
  const { data, refetch, isFetching, dataUpdatedAt } = useGetDatasetDetail(
    datasetId,
    { staleTime: 0 },
    TABLE_PARAMS,
  );
  const [tab, setTab] = useState("inferred");
  const [preset, setPreset] = useState("inferred");
  const [expanded, setExpanded] = useState(() => new Set());
  const [refreshedAt, setRefreshedAt] = useState(null);
  const [format, setFormat] = useState("json");
  const [draft, setDraft] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const { addOp } = useChangeSet(datasetId);

  const rows = useMemo(() => data?.table ?? [], [data]);
  const total = data?.metadata?.total_rows ?? rows.length;
  const config = useMemo(() => data?.dataset_config ?? {}, [data]);
  const schema = config.schema ?? null;
  const columns = useMemo(
    () => (data?.column_config ?? []).filter((c) => isUserColumn(c) && c.id),
    [data],
  );

  const inferred = useMemo(() => {
    const valueRows = rows.map((r) =>
      Object.fromEntries(columns.map((c) => [c.id, cellValue(r, c.id)])),
    );
    const entries = inferColumns(columns, valueRows);
    const nested = {};
    columns.forEach((c) => {
      if (
        !JSON_TYPES.has(c.data_type) &&
        !entries.find((e) => e.column_id === c.id)?.kinds?.object
      )
        return;
      nested[c.name] = inferPaths(
        rows.map((r) => cellValue(r, c.id)),
        { column: c.name, edgeIndexes: false, maxDepth: 4, maxPaths: 60 },
      );
    });
    return { entries, nested };
  }, [rows, columns]);

  const declared = schema?.declared ?? null;
  // A declared schema opens on its own tab (enforce lives there).
  const hasDeclared = Boolean(declared);
  useEffect(() => {
    if (hasDeclared) setTab("declared");
  }, [hasDeclared]);
  const editorText = useMemo(() => {
    if (!declared) return "";
    return format === "yaml"
      ? yamlStringify(declared)
      : JSON.stringify(declared, null, 2);
  }, [declared, format]);
  const text = draft ?? editorText;
  const parsedDraft = useMemo(() => {
    if (!text.trim()) return { ok: false, error: "Empty schema" };
    try {
      const v = format === "yaml" ? parseYaml(text) : JSON.parse(text);
      const problems = schemaProblems(v);
      return problems.length
        ? { ok: false, error: problems[0] }
        : { ok: true, value: v };
    } catch (e) {
      return { ok: false, error: String(e?.message ?? e).split("\n")[0] };
    }
  }, [text, format]);

  const results = useMemo(() => {
    if (!declared) return [];
    const cols = columns.map((c) => ({
      name: c.name,
      json: JSON_TYPES.has(c.data_type),
      number: NUMBER_TYPES.has(c.data_type),
      boolean: c.data_type === "boolean",
    }));
    return rows.map((r) => {
      const byName = Object.fromEntries(
        columns.map((c) => [c.name, cellValue(r, c.id)]),
      );
      return {
        rowId: r.row_id,
        ...validateRow(byName, declared, { columns: cols }),
      };
    });
  }, [declared, rows, columns]);
  const failing = results.filter((r) => !r.ok);
  const groups = useMemo(() => groupIssues(failing), [failing]);
  const mode = enforceModeOf(schema);
  // `Row 31` labels of failing rows, in grid order.
  const failingLabels = useMemo(() => {
    const at = new Map(rows.map((r, i) => [r.row_id, i]));
    return failing.map((f) => `Row ${(at.get(f.rowId) ?? 0) + 1}`);
  }, [failing, rows]);

  const save = useMutation({
    /** @param {{ declared: object, enforce: string, preset?: string }} next */
    mutationFn: (next) =>
      axios.put(
        apiPath("/model-hub/datasets/{dataset_id}/schema/", {
          dataset_id: datasetId,
        }),
        {
          declared: next.declared,
          enforce: next.enforce,
          preset: next.preset ?? "",
        },
      ),
    /** @param {{ message?: unknown, statusCode?: number }} error Shared-client normalized error. */
    onError: (error) => {
      const message = getRequestErrorMessage(
        error,
        "Schema could not be saved. Check its definition and try again.",
      );
      enqueueSnackbar(
        typeof message === "string"
          ? message
          : "Schema could not be saved. Check its definition and try again.",
        { variant: "error" },
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["get-dataset-detail", datasetId],
      });
      refreshGrid?.(null, true);
    },
  });

  useEffect(() => setDraft(null), [format, declared]);

  const declare = () => {
    const next =
      preset === "inferred"
        ? schemaFromInferred(inferred.entries, inferred.nested)
        : presetById(preset)?.schema;
    save.mutate(
      { declared: next, enforce: "off", preset },
      {
        onSuccess: () => {
          setTab("declared");
          enqueueSnackbar("Schema declared", { variant: "success" });
        },
      },
    );
  };

  const setEnforce = (next) => {
    if (!declared) return;
    const target = next === true ? "check" : next === false ? "off" : next;
    // W3-2: Reject is turned on only after the rows are checked and the
    // count is confirmed; a big Flag share asks first too.
    if (target === "reject" && !confirm) {
      setConfirm({ count: failing.length, mode: "reject" });
      return;
    }
    if (
      target === "check" &&
      total &&
      failing.length / total > 0.2 &&
      !confirm
    ) {
      setConfirm({ count: failing.length, mode: "check" });
      return;
    }
    setConfirm(null);
    save.mutate(
      { ...schema, enforce: target },
      {
        onSuccess: () =>
          enqueueSnackbar(
            target === "reject"
              ? "Edits and imports that don't match are rejected"
              : target === "check"
                ? "Schema preview enabled"
                : "Schema check off",
            {
              variant: "success",
            },
          ),
      },
    );
  };

  const saveDraft = () => {
    if (!parsedDraft.ok) return;
    save.mutate(
      { ...schema, declared: parsedDraft.value },
      {
        onSuccess: () => {
          setDraft(null);
          enqueueSnackbar("Schema saved", { variant: "success" });
        },
      },
    );
  };

  const name = data?.metadata?.dataset_name ?? "dataset";
  const sampledText =
    refreshedAt && dataUpdatedAt >= refreshedAt
      ? "Sampled just now"
      : `sampled ${rows.length.toLocaleString("en-US")} of ${total.toLocaleString("en-US")} rows`;

  return (
    <Drawer
      anchor="right"
      open
      onClose={onClose}
      PaperProps={{
        sx: {
          width: WIDTH,
          maxWidth: "100vw",
          display: "flex",
          flexDirection: "column",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 2,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          Schema
        </Typography>
        <Typography typography="s2" color="text.secondary" sx={{ flex: 1 }}>
          {data ? `· ${sampledText}` : "· sampling rows…"}
        </Typography>
        <Button
          size="small"
          variant="text"
          color="inherit"
          disabled={isFetching}
          startIcon={<Iconify icon="mdi:refresh" width={16} />}
          onClick={() => {
            setRefreshedAt(Date.now());
            refetch();
          }}
          sx={{ typography: "s2" }}
        >
          Refresh
        </Button>
        <IconButton size="small" aria-label="Close" onClick={onClose}>
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ px: 2, py: 1.25 }}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={tab}
          onChange={(_, v) => v && setTab(v)}
          sx={{
            "& .MuiToggleButton-root": { py: 0.25, px: 1.25, typography: "s2" },
          }}
        >
          <ToggleButton value="inferred">Inferred</ToggleButton>
          <ToggleButton value="declared">Declared</ToggleButton>
        </ToggleButtonGroup>
        <Box sx={{ flex: 1 }} />
        <Select
          size="small"
          value={preset}
          onChange={(e) => setPreset(e.target.value)}
          aria-label="Start from"
          sx={{
            typography: "s2",
            minWidth: 150,
            "& .MuiSelect-select": { py: 0.5 },
          }}
          renderValue={(v) =>
            v === "inferred" ? "From inferred" : presetById(v)?.name
          }
        >
          <MenuItem value="inferred" sx={{ typography: "s2" }}>
            From inferred
          </MenuItem>
          {SCHEMA_PRESETS.map((p) => (
            <MenuItem key={p.id} value={p.id} sx={{ typography: "s2" }}>
              <Stack>
                <span>{p.name}</span>
                <Typography typography="s3" color="text.secondary">
                  {p.hint}
                </Typography>
              </Stack>
            </MenuItem>
          ))}
        </Select>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!data || save.isPending}
          onClick={declare}
        >
          Declare
        </Button>
      </Stack>
      <Box sx={{ flex: 1, overflow: "auto", px: 2, pb: 2 }}>
        {tab === "inferred" ? (
          <Box
            component="table"
            sx={{
              width: "100%",
              borderCollapse: "collapse",
              tableLayout: "fixed",
            }}
          >
            <Box component="thead">
              <Box
                component="tr"
                sx={{
                  "& th": {
                    textAlign: "left",
                    typography: "s3",
                    fontWeight: 500,
                    color: "text.secondary",
                    py: 0.75,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    position: "sticky",
                    top: 0,
                    bgcolor: "background.paper",
                    zIndex: 1,
                  },
                }}
              >
                <Box component="th" sx={{ pl: 1, width: "34%" }}>
                  Path
                </Box>
                <Box component="th" sx={{ width: "22%" }}>
                  Kinds
                </Box>
                <Box component="th" sx={{ width: "12%" }}>
                  Rows
                </Box>
                <Box component="th" sx={{ width: "16%" }}>
                  Example
                </Box>
                <Box component="th">Values</Box>
              </Box>
            </Box>
            <Box component="tbody">
              {inferred.entries.map((e) => {
                const nested = inferred.nested[e.path] ?? [];
                const isOpen = expanded.has(e.path);
                return (
                  <React.Fragment key={e.column_id}>
                    <InferredRow
                      entry={e}
                      expandable={nested.length > 0}
                      open={isOpen}
                      onToggle={() =>
                        setExpanded((s) => {
                          const n = new Set(s);
                          if (n.has(e.path)) n.delete(e.path);
                          else n.add(e.path);
                          return n;
                        })
                      }
                    />
                    {isOpen &&
                      nested.map((p) => (
                        <InferredRow key={p.path} entry={p} depth={1} />
                      ))}
                  </React.Fragment>
                );
              })}
            </Box>
          </Box>
        ) : !declared ? (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ py: 3, textAlign: "center" }}
          >
            No schema yet. Pick a starting point and press Declare.
          </Typography>
        ) : (
          <Stack gap={1.5}>
            <Stack direction="row" alignItems="center" gap={1}>
              <ToggleButtonGroup
                size="small"
                exclusive
                value={format}
                onChange={(_, v) => v && setFormat(v)}
                sx={{
                  "& .MuiToggleButton-root": {
                    py: 0.125,
                    px: 1,
                    typography: "s3",
                  },
                }}
              >
                <ToggleButton value="json">JSON</ToggleButton>
                <ToggleButton value="yaml">YAML</ToggleButton>
              </ToggleButtonGroup>
              <Typography
                typography="s3"
                color={parsedDraft.ok ? "text.secondary" : "error.main"}
                noWrap
                sx={{ flex: 1, minWidth: 0 }}
              >
                {parsedDraft.ok
                  ? schema?.preset && schema.preset !== "inferred"
                    ? `From ${presetById(schema.preset)?.name ?? "inferred"}`
                    : "From inferred"
                  : `Invalid schema · ${parsedDraft.error}`}
              </Typography>
              {draft !== null && draft !== editorText && (
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!parsedDraft.ok || save.isPending}
                  onClick={saveDraft}
                >
                  Save
                </Button>
              )}
              <Button
                size="small"
                variant="text"
                color="inherit"
                startIcon={<Iconify icon="mdi:download-outline" width={16} />}
                onClick={() =>
                  downloadJson(`${name}-schema.json`, {
                    $schema: "https://json-schema.org/draft/2020-12/schema",
                    title: name,
                    ...declared,
                  })
                }
                sx={{ typography: "s3" }}
              >
                Export JSON Schema
              </Button>
            </Stack>
            <Box
              sx={{
                height: 260,
                border: "1px solid",
                borderColor: parsedDraft.ok ? "divider" : "error.main",
                borderRadius: 1,
                overflow: "hidden",
              }}
            >
              <TextField
                fullWidth
                multiline
                rows={10}
                disabled={save.isPending}
                value={text}
                onChange={(event) => setDraft(event.target.value)}
                inputProps={{
                  "aria-label": "Declared schema",
                  spellCheck: false,
                }}
                sx={{ "& textarea": { fontFamily: MONO, fontSize: "0.75rem" } }}
              />
            </Box>
            <Stack gap={0.5}>
              <Typography typography="s2" fontWeight="fontWeightMedium">
                When a row doesn&apos;t match
              </Typography>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={mode}
                onChange={(_, v) => v && v !== mode && setEnforce(v)}
                disabled={save.isPending}
                aria-label="Schema enforcement"
                sx={{ flexWrap: "wrap" }}
              >
                <ToggleButton value="off" sx={{ typography: "s3", px: 1.25 }}>
                  Off
                </ToggleButton>
                <ToggleButton value="check" sx={{ typography: "s3", px: 1.25 }}>
                  Preview mismatches
                </ToggleButton>
                <ToggleButton
                  value="reject"
                  sx={{ typography: "s3", px: 1.25 }}
                >
                  Reject edits and imports that don&apos;t match
                </ToggleButton>
              </ToggleButtonGroup>
            </Stack>
            {mode !== "off" && (
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
                  sx={{ px: 1.5, py: 1, bgcolor: "background.neutral" }}
                >
                  <Iconify
                    icon={
                      failing.length
                        ? "mdi:alert-outline"
                        : "mdi:check-circle-outline"
                    }
                    width={16}
                    sx={{ color: failing.length ? "amber.500" : "green.600" }}
                  />
                  <Typography
                    typography="s2"
                    fontWeight="fontWeightMedium"
                    sx={{ flex: 1 }}
                  >
                    {failing.length
                      ? `${failing.length.toLocaleString("en-US")} ${failing.length === 1 ? "row fails" : "rows fail"} the schema`
                      : "Every sampled row passes the schema"}
                  </Typography>
                  {failing.length > 0 && (
                    <Button
                      size="small"
                      variant="text"
                      onClick={() => {
                        setInspectedRowIds(failing.map((row) => row.rowId));
                      }}
                      sx={{ typography: "s3" }}
                    >
                      View all
                    </Button>
                  )}
                </Stack>
                {groups.map((g) => (
                  <Stack
                    key={g.message}
                    direction="row"
                    alignItems="center"
                    gap={1}
                    sx={{
                      px: 1.5,
                      py: 0.75,
                      borderTop: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    <Typography
                      typography="s2"
                      sx={{
                        flex: 1,
                        minWidth: 0,
                        fontFamily: MONO,
                        wordBreak: "break-word",
                      }}
                    >
                      {g.message}
                    </Typography>
                    <Typography
                      typography="s3"
                      color="text.secondary"
                      sx={{ whiteSpace: "nowrap" }}
                    >
                      {`${g.rows.length} ${g.rows.length === 1 ? "row" : "rows"}`}
                    </Typography>
                    <Button
                      size="small"
                      variant="text"
                      onClick={() => {
                        setInspectedRowIds(g.rows);
                      }}
                      sx={{
                        typography: "s3",
                        minWidth: 0,
                        whiteSpace: "nowrap",
                      }}
                    >
                      View rows
                    </Button>
                  </Stack>
                ))}
              </Box>
            )}
          </Stack>
        )}
        {isSurfaceShown("dataset.imports.transforms") &&
          isSurfaceShown("dataset.versions") &&
          schema?.transforms && (
            <TransformsSection
              columns={columns}
              rows={rows}
              valueOf={cellValue}
              schema={schema}
              saving={save.isPending}
              onSave={(transforms) =>
                save.mutate(
                  { ...(schema ?? { enforce: "off" }), transforms },
                  {
                    onSuccess: () =>
                      enqueueSnackbar("Transformations saved", {
                        variant: "success",
                      }),
                  },
                )
              }
              onApply={(ops) => {
                ops.forEach((op) => addOp(op));
                enqueueSnackbar(
                  `${ops.length} ${ops.length === 1 ? "cell" : "cells"} changed in pending changes`,
                  { variant: "success" },
                );
              }}
            />
          )}
      </Box>
      <SchemaMismatchRows
        rows={rows}
        columns={columns}
        rowIds={inspectedRowIds}
        onClose={() => setInspectedRowIds(null)}
      />
      <Dialog
        open={Boolean(confirm)}
        onClose={() => setConfirm(null)}
        maxWidth="xs"
      >
        <DialogContent>
          {confirm?.mode === "reject" ? (
            <Stack gap={1} data-testid="schema-reject-check">
              <Typography typography="s1" fontWeight={600}>
                Reject rows that don&apos;t match?
              </Typography>
              <Typography typography="s2">
                {`Checked ${results.length.toLocaleString("en-US")} rows · ${
                  confirm.count
                    ? `${confirm.count.toLocaleString("en-US")} don't match (${failingLabels.slice(0, 5).join(", ")}${failingLabels.length > 5 ? ", …" : ""})`
                    : "every row matches"
                }`}
                {confirm.count > 0 && (
                  <Button
                    size="small"
                    variant="text"
                    onClick={() => {
                      setConfirm(null);
                      setInspectedRowIds(failing.map((row) => row.rowId));
                    }}
                    sx={{ typography: "s3", ml: 0.5, minWidth: 0 }}
                  >
                    Show them
                  </Button>
                )}
              </Typography>
              <Typography typography="s3" color="text.secondary">
                Rows already saved stay as they are. From now on, a save, import
                or paste that breaks the schema is refused.
              </Typography>
            </Stack>
          ) : (
            <Typography typography="s1">
              {confirm
                ? `${confirm.count.toLocaleString("en-US")} of ${rows.length.toLocaleString("en-US")} sampled rows fail this schema. Preview mismatches without rejecting writes?`
                : ""}
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setConfirm(null)}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={() => {
              const next = confirm?.mode === "reject" ? "reject" : "check";
              setConfirm(null);
              save.mutate({ ...schema, enforce: next });
            }}
          >
            {confirm?.mode === "reject"
              ? "Reject from now on"
              : "Enable preview"}
          </Button>
        </DialogActions>
      </Dialog>
    </Drawer>
  );
};

SchemaDrawerBody.propTypes = {
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
};

SchemaDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
};

export default SchemaDrawer;
