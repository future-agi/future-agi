import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import axios, { endpoints } from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { gridWritesClassic } from "src/sections/develop/realBackendCaps";
import { notify } from "src/sections/common/ux/toast";
import {
  createdColumnsText,
  useChangeSetStore,
  useCreatedColumnsStore,
} from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { useDatasetColumns } from "src/sections/develop-detail/DataTab/DatapointDrawerV2/itemHooks";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { parsePasted } from "./pasteRows";
import {
  CREATE,
  META_PREFIX,
  SKIP,
  addLabel,
  blockedReason,
  defaultMapping,
  mappedRows,
  mappingSummary,
  schemaRejected,
  transformRows,
} from "./pasteMapping";
import { useGetDatasetDetail } from "src/api/develop/develop-detail";

const PREVIEW_ROWS = 5;
const cellSx = {
  px: 1,
  py: 0.5,
  borderBottom: "1px solid",
  borderColor: "divider",
  typography: "s3",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
  maxWidth: 220,
};

const detectedTypeOf = (values = []) => {
  const value = values.find((item) => String(item ?? "").trim() !== "");
  if (value == null) return "text";
  const raw = String(value).trim();
  if (/^(true|false)$/i.test(raw)) return "bool";
  if (raw !== "" && Number.isFinite(Number(raw))) return "number";
  try {
    const parsed = JSON.parse(raw);
    if (parsed !== null && typeof parsed === "object") return "json";
  } catch {
    // Plain text is the normal fallback.
  }
  return "text";
};

/**
 * IT-R1 G5 P3 + IT-R3 G5 P4: `Add rows ▾ › Paste`. Paste CSV, TSV, a JSON
 * array or JSON lines. Under the text: the first 5 parsed rows and one line
 * per pasted header → [existing column | metadata key | Create column |
 * Skip], guessed by name and meaning. The button states the result (`Add 2
 * rows · 3 new columns`); classic rows persist immediately, prototype
 * rows land as pending changes. New columns are written before the rows.
 */
const PasteRowsDialog = ({ open, onClose, datasetId }) => {
  const [text, setText] = useState("");
  const [mapping, setMapping] = useState({});
  const [busy, setBusy] = useState(false);
  const classic = gridWritesClassic();
  // Column creation persists independently of the row batch. Keep successful
  // creates during this attempt so a rejected row write can be retried.
  const createdByName = useRef(new Map());
  useEffect(() => {
    createdByName.current.clear();
  }, [datasetId, open]);
  const queryClient = useQueryClient();
  const { refreshGrid } = useDevelopDetailContext() ?? {};
  const { data: allColumns = [] } = useDatasetColumns(
    open ? datasetId : undefined,
  );
  // Only user columns take pasted values (not run outputs or eval scores).
  const columns = useMemo(
    () =>
      (allColumns ?? []).filter(
        (c) =>
          !["output", "eval", "human", "annotation"].includes(
            String(c.role ?? "").toLowerCase(),
          ) &&
          !["run_prompt", "evaluation", "experiment"].includes(
            String(c.origin_type ?? "").toLowerCase(),
          ),
      ),
    [allColumns],
  );
  const parsed = useMemo(() => parsePasted(text), [text]);
  const headers = parsed.columns;
  const headerKey = headers.join("\u0001");
  const columnKey = columns.map((c) => c.id).join(",");
  // A new header line (or the columns loading) re-guesses the mapping.
  useEffect(() => {
    setMapping(defaultMapping(headers, columns, { allowMetadata: !classic }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [headerKey, columnKey, classic]);
  // W3-2 Reject: rows that break the declared schema are not added.
  const { data: detail } = useGetDatasetDetail(
    open ? datasetId : undefined,
    {},
    { page_size: 1, current_page_index: 0 },
  );
  const schema = detail?.dataset_config?.schema ?? null;
  const rejected = useMemo(
    () =>
      schemaRejected(
        mappedRows(parsed.rows, headers, mapping, {}),
        columns,
        schema,
      ),
    [parsed.rows, headers, mapping, columns, schema],
  );
  const rejectedSet = useMemo(
    () => new Set(rejected.map((r) => r.index)),
    [rejected],
  );
  const n = parsed.rows.length - rejected.length;
  const summary = mappingSummary(headers, mapping, columns);
  const reason =
    classic && summary.metadata.length
      ? "Row metadata cannot be saved here. Map metadata fields to columns or Skip."
      : rejected.length && !n
        ? "Every pasted row breaks the schema"
        : blockedReason({ text, error: parsed.error, n, summary });

  const add = async () => {
    if (reason || busy) return;
    setBusy(true);
    try {
      const created = {};
      for (const h of summary.create) {
        const name = h || "column";
        let column = createdByName.current.get(name);
        if (!column) {
          const res = await axios.post(endpoints.develop.addColumn(datasetId), {
            new_column_name: name,
            column_type: "text",
          });
          column = { name, id: res?.data?.result?.column_id };
          createdByName.current.set(name, column);
        }
        if (classic) {
          // add_static_column returns a success string, not an id. This key
          // is local to mapping; add_rows receives the column's name below.
          created[h] = `paste:new:${headers.indexOf(h)}`;
        } else {
          if (!column.id) {
            const res = await axios.get(
              endpoints.develop.getDatasetDetail(datasetId),
              { params: { column_config_only: true } },
            );
            column.id = res?.data?.result?.column_config?.find(
              (c) => c.name === name,
            )?.id;
          }
          if (!column.id)
            throw new Error(`Couldn't find the newly created column "${name}".`);
          created[h] = column.id;
        }
      }
      const createdColumns = Object.entries(created).map(([h, id]) => ({
        id,
        name: h || "column",
      }));
      const rowColumns = [...columns, ...createdColumns];
      const rows = transformRows(
        mappedRows(parsed.rows, headers, mapping, created),
        rowColumns,
        schema,
      ).rows.filter((_, i) => !rejectedSet.has(i));
      if (classic) {
        const names = new Map(rowColumns.map((c) => [String(c.id), c.name]));
        await axios.post(
          apiPath("/model-hub/develops/{dataset_id}/add_rows/", {
            dataset_id: datasetId,
          }),
          {
            rows: rows.map((r) => ({
              cells: Object.entries(r.cells).map(([id, value]) => {
                const name = names.get(id);
                if (!name)
                  throw new Error("A mapped column is no longer available.");
                return { column_name: name, value };
              }),
            })),
          },
        );
      } else {
        const stamp = Date.now().toString(36);
        const cs = useChangeSetStore.getState();
        rows.forEach((r, i) => {
          const accepted = cs.addOp(datasetId, {
            op: "add_row",
            temp_id: `new-${stamp}-${i + 1}`,
            cells: r.cells,
            ...(Object.keys(r.metadata).length ? { metadata: r.metadata } : {}),
            split_ids: [],
            // integration (DI-03): pasted rows keep where they came from.
            source: { type: "paste", label: "Pasted" },
          });
          if (accepted === false)
            throw new Error("Pasted rows were not added to pending changes.");
        });
        // Only prototype pending rows participate in Discard's column cleanup.
        createdColumns.forEach((column) =>
          useCreatedColumnsStore
            .getState()
            .add(datasetId, { ...column, from: "paste" }),
        );
      }
      if (classic || summary.create.length) {
        queryClient.invalidateQueries({
          predicate: (q) => JSON.stringify(q.queryKey).includes(datasetId),
        });
        refreshGrid?.();
      }
      const cols = summary.create.length
        ? ` · ${createdColumnsText(createdColumns)} created now${
            classic ? "" : " (columns are not versioned)"
          }`
        : "";
      notify(
        `${n} pasted ${n === 1 ? "row" : "rows"} added${classic ? "" : " to pending changes"}${cols}`,
        { variant: "success", key: "rows-pasted" },
      );
      setText("");
      onClose(true);
    } catch (e) {
      notify(
        `Couldn't add the pasted rows: ${e?.response?.data?.result ?? e?.message ?? "try again"}`,
        { variant: "error", key: "rows-pasted-error" },
      );
    } finally {
      setBusy(false);
    }
  };

  const targetLabel = (t) => {
    if (t === CREATE) return "new column";
    if (t === SKIP) return "Skip";
    if (String(t).startsWith(META_PREFIX))
      return `metadata.${String(t).slice(META_PREFIX.length)}`;
    return columns.find((c) => c.id === t)?.name ?? t;
  };

  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!busy) onClose(false);
      }}
      maxWidth="md"
      fullWidth
      aria-labelledby="paste-rows-title"
    >
      <DialogTitle id="paste-rows-title" sx={{ pr: 6 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          Paste rows
        </Typography>
        <Typography typography="s2" color="text.secondary">
          CSV, TSV (a copy from a sheet), a JSON array or JSON lines. The first
          line (or the keys) names the columns.
        </Typography>
        <IconButton
          aria-label="Close"
          disabled={busy}
          onClick={() => onClose(false)}
          sx={{ position: "absolute", right: 12, top: 12 }}
        >
          <Iconify icon="mingcute:close-line" />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        <TextField
          autoFocus
          multiline
          minRows={headers.length ? 3 : 6}
          maxRows={headers.length ? 6 : 14}
          fullWidth
          value={text}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          placeholder={`customer_message,expected_reply\nWhere is order A-4410?,It ships tomorrow.`}
          inputProps={{
            "aria-label": "Pasted rows",
            spellCheck: false,
            "data-testid": "paste-rows-text",
          }}
          sx={{ "& textarea": { fontFamily: "monospace", fontSize: 12 } }}
        />
        {parsed.error ? (
          <Typography
            typography="s2"
            color="error.main"
            sx={{ mt: 1 }}
            data-testid="paste-rows-error"
          >
            {parsed.error}
          </Typography>
        ) : null}
        {rejected.length > 0 && (
          <Typography
            typography="s2"
            color="error.main"
            sx={{ mt: 1 }}
            data-testid="paste-rows-schema"
          >
            {`${rejected.length === 1 ? "1 row doesn't" : `${rejected.length} rows don't`} match the schema and won't be added · ${rejected
              .slice(0, 3)
              .map((r) => `pasted row ${r.index + 1}: ${r.issues[0].message}`)
              .join(" · ")}`}
          </Typography>
        )}
        {headers.length ? (
          <Stack
            spacing={1.5}
            sx={{ mt: 1.5 }}
            data-testid="paste-rows-summary"
          >
            <Box>
              <Typography typography="s2" fontWeight={600} sx={{ mb: 0.5 }}>
                {`${n} ${n === 1 ? "row" : "rows"} · ${headers.length} ${
                  headers.length === 1 ? "column" : "columns"
                } · ${summary.create.length} new ${
                  summary.create.length === 1 ? "column" : "columns"
                }`}
              </Typography>
              <Box
                sx={{
                  overflowX: "auto",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 0.5,
                }}
              >
                <Box
                  component="table"
                  sx={{ borderCollapse: "collapse", width: "100%" }}
                  data-testid="paste-rows-preview"
                >
                  <thead>
                    <tr>
                      {headers.map((h) => (
                        <Box
                          component="th"
                          key={h}
                          sx={{
                            ...cellSx,
                            textAlign: "left",
                            fontWeight: 600,
                            bgcolor: "background.neutral",
                          }}
                        >
                          {h}
                        </Box>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {parsed.rows.slice(0, PREVIEW_ROWS).map((r, i) => (
                      <tr key={i}>
                        {headers.map((h, j) => (
                          <Box
                            component="td"
                            key={h}
                            sx={cellSx}
                            title={String(r?.[j] ?? "")}
                          >
                            {String(r?.[j] ?? "")}
                          </Box>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </Box>
              </Box>
            </Box>
            <Box>
              <Typography typography="s2" fontWeight={600} sx={{ mb: 0.5 }}>
                Where each pasted column goes
              </Typography>
              <Stack spacing={0.5} data-testid="paste-rows-mapping">
                {headers.map((h, columnIndex) => {
                  const t = mapping[h] ?? SKIP;
                  const detectedType = detectedTypeOf(
                    parsed.rows.map((row) => row?.[columnIndex]),
                  );
                  return (
                    <Stack key={h} direction="row" alignItems="center" gap={1}>
                      <Typography
                        typography="s2"
                        sx={{
                          width: 180,
                          flexShrink: 0,
                          fontFamily: "monospace",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {h}
                      </Typography>
                      <Chip
                        size="small"
                        label={detectedType}
                        sx={{ height: 20, typography: "s3", flexShrink: 0 }}
                      />
                      <Iconify
                        icon="mdi:arrow-right"
                        width={14}
                        sx={{ color: "text.disabled", flexShrink: 0 }}
                      />
                      <Select
                        size="small"
                        value={t}
                        disabled={busy}
                        onChange={(e) =>
                          setMapping((m) => ({ ...m, [h]: e.target.value }))
                        }
                        renderValue={targetLabel}
                        inputProps={{ "aria-label": `Where ${h} goes` }}
                        sx={{ minWidth: 240, typography: "s2" }}
                      >
                        {columns.map((c) => (
                          <MenuItem key={c.id} value={c.id}>
                            {c.name}
                            {c.role ? (
                              <Typography
                                component="span"
                                typography="s3"
                                color="text.secondary"
                                sx={{ ml: 1 }}
                              >
                                {String(c.role).toLowerCase()}
                              </Typography>
                            ) : null}
                          </MenuItem>
                        ))}
                        <MenuItem value={`${META_PREFIX}${h}`}>
                          {`metadata.${h}`}
                        </MenuItem>
                        <MenuItem value={CREATE}>
                          {`Create column “${h}”`}
                        </MenuItem>
                        <MenuItem value={SKIP}>Skip</MenuItem>
                      </Select>
                      {(t === CREATE || t === SKIP) && (
                        <Stack direction="row" alignItems="center" gap={0.25}>
                          <Typography typography="s3" color="text.secondary">
                            new column
                          </Typography>
                          <Switch
                            size="small"
                            checked={t === CREATE}
                            disabled={busy}
                            onChange={(event) =>
                              setMapping((m) => ({
                                ...m,
                                [h]: event.target.checked ? CREATE : SKIP,
                              }))
                            }
                            inputProps={{ "aria-label": `New column ${h}` }}
                            sx={{ ml: -0.5 }}
                          />
                        </Stack>
                      )}
                    </Stack>
                  );
                })}
              </Stack>
            </Box>
          </Stack>
        ) : null}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        {reason && text.trim() ? (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ mr: "auto" }}
            data-testid="paste-rows-reason"
          >
            {reason}
          </Typography>
        ) : null}
        <Button color="inherit" disabled={busy} onClick={() => onClose(false)}>
          Cancel
        </Button>
        <CustomTooltip show={Boolean(reason)} title={reason} size="small">
          <span>
            <LoadingButton
              variant="contained"
              color="primary"
              disabled={Boolean(reason)}
              loading={busy}
              onClick={add}
              data-testid="paste-rows-add"
            >
              {n ? addLabel(n, summary) : "Add rows"}
            </LoadingButton>
          </span>
        </CustomTooltip>
      </DialogActions>
    </Dialog>
  );
};

PasteRowsDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  datasetId: PropTypes.string,
};

export default PasteRowsDialog;
