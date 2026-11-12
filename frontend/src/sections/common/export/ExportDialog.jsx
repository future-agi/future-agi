// Download… dialogs (W3-6 B2 / B3). One shell, two sources:
//   DatasetExportDialog  a dataset version: every row, a split or the Data
//                        tab's filter, in six formats (datasetExport.js)
//   ResultsExportDialog  the rows of the current results view, or every
//                        row of its runs (resultsExport.js)
// Every file is written by writers.js.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  LinearProgress,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { notify } from "src/sections/common/ux/toast";
import { fmtCount } from "src/sections/common/format/fmt";
import { latestOf, useDatasetVersions } from "src/api/develop/dataset-versions";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { runResultsKey, runResultsParams } from "src/api/develop/run-results";
import { byteLength, EXPORT_FORMATS, sizeText, hasExpected } from "./writers";
import {
  countDatasetRows,
  datasetFile,
  datasetRecords,
  defaultMessageRoles,
  fetchDatasetRows,
  formatBlocker,
  generatedColumnsOf,
  rowMetadataKeys,
  saveFile,
  userColumnsOf,
} from "./datasetExport";
import {
  effectiveSort,
  everyRowView,
  fetchResultsExport,
  resultsFile,
  resultsMeta,
  resultsRecords,
  viewFromGridReads,
  viewIsNarrowed,
  viewParts,
} from "./resultsExport";

const DATASET_FORMATS = [
  ["csv", "One row per line; generated columns and reasons flat"],
  ["json", "One document: rows grouped by role, with a header"],
  ["jsonl", "One row per line, fields grouped by role"],
  ["parquet", "Columnar, typed; for pandas, DuckDB, Spark"],
  ["openai_ft", "Chat messages for OpenAI fine-tuning"],
  ["openai_evals", "Prompt messages + ideal answer for OpenAI Evals"],
];
const RESULTS_FORMATS = [
  ["csv", "Spreadsheet; one column per run and eval"],
  ["json", "One document with a header naming runs, baseline and filters"],
  ["jsonl", "One row per line; runs, evals, reasons and trials nested"],
];

const labelSx = { typography: "s2", color: "text.secondary", mb: 0.5 };

function FormatList({ formats, value, onChange, blockers = {} }) {
  return (
    <RadioGroup
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Format"
    >
      {formats.map(([id, hint]) => (
        <FormControlLabel
          key={id}
          value={id}
          disabled={Boolean(blockers[id])}
          control={<Radio size="small" />}
          sx={{
            alignItems: "flex-start",
            mr: 0,
            "& .MuiRadio-root": { pt: 0.25 },
          }}
          label={
            <Box sx={{ py: 0.25 }}>
              <Typography typography="s1">
                {EXPORT_FORMATS[id].label}
              </Typography>
              <Typography typography="s3" color="text.secondary">
                {blockers[id] || hint}
              </Typography>
            </Box>
          }
        />
      ))}
    </RadioGroup>
  );
}

FormatList.propTypes = {
  formats: PropTypes.array.isRequired,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  blockers: PropTypes.object,
};

function Shell({
  open,
  onClose,
  title,
  children,
  footer,
  busy,
  onDownload,
  canDownload,
  testId,
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      data-testid={testId}
    >
      <DialogTitle sx={{ typography: "m3", pb: 1 }}>{title}</DialogTitle>
      <DialogContent
        dividers
        sx={{ display: "flex", flexDirection: "column", gap: 2 }}
      >
        {children}
      </DialogContent>
      <DialogActions
        sx={{ px: 3, py: 1.5, gap: 1, justifyContent: "space-between" }}
      >
        <Box sx={{ minWidth: 0, flex: 1 }}>{footer}</Box>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          loading={busy}
          disabled={!canDownload}
          onClick={onDownload}
          data-testid="export-download"
        >
          Download
        </LoadingButton>
      </DialogActions>
    </Dialog>
  );
}

Shell.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  title: PropTypes.node,
  children: PropTypes.node,
  footer: PropTypes.node,
  busy: PropTypes.bool,
  onDownload: PropTypes.func,
  canDownload: PropTypes.bool,
  testId: PropTypes.string,
};

const versionLabel = (v, latest) =>
  [`v${v.number}`, v.id === latest?.id ? "latest" : null, ...(v.tags ?? [])]
    .filter(Boolean)
    .join(" · ");

function RoleSelect({ label, value, onChange, columns, optional, testId }) {
  return (
    <Stack direction="row" alignItems="center" gap={1.5}>
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ width: 140, flexShrink: 0 }}
      >
        {label}
      </Typography>
      <Select
        size="small"
        value={value ?? ""}
        displayEmpty
        onChange={(e) => onChange(e.target.value || null)}
        sx={{ flex: 1, typography: "s2" }}
        inputProps={{ "aria-label": label, "data-testid": testId }}
      >
        {optional && <MenuItem value="">None</MenuItem>}
        {!optional && !value && (
          <MenuItem value="" disabled>
            Pick a column
          </MenuItem>
        )}
        {columns.map((c) => (
          <MenuItem key={c.id} value={c.id}>
            {c.name}
            {c.role ? ` · ${c.role}` : ""}
          </MenuItem>
        ))}
      </Select>
    </Stack>
  );
}

RoleSelect.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  columns: PropTypes.array.isRequired,
  optional: PropTypes.bool,
  testId: PropTypes.string,
};

/**
 * Dataset `Download…`: format, version, rows (all · a split · the current
 * filter), generated columns, row metadata, the size, Download.
 * @param {{open: boolean, onClose: Function, datasetId: string,
 *   datasetName?: string, currentFilter?: {filters: object[], search?: string,
 *   label?: string}|null, versionNumber?: number|null}} props
 */
export function DatasetExportDialog(props) {
  // MAIN-RULING-EXPORT-CONTRACT: ordinary CSV is always available. The
  // dataset.export gate applies only to advanced formats, not current-row
  // reads or their failure/retry UI.
  const classic = datasetCaps().real;
  if (classic && !props.open) return null;
  return (
    <DatasetExportContent
      {...props}
      currentFilter={classic ? null : props.currentFilter}
      versionNumber={classic ? null : props.versionNumber}
    />
  );
}

function DatasetExportContent({
  open,
  onClose,
  datasetId,
  datasetName: nameProp,
  currentFilter = null,
  versionNumber = null,
}) {
  const classic = datasetCaps().real;
  const versioned = !classic && datasetCaps().saveVersion;
  const versionsQ = useDatasetVersions(datasetId, {
    enabled: open && Boolean(datasetId) && versioned,
  });
  // Ignore even cached version data when launch capabilities hide versions.
  const versions = useMemo(
    () => (versioned ? versionsQ.data ?? [] : []),
    [versioned, versionsQ.data],
  );
  const latest = latestOf(versions);
  const advancedFormats = isSurfaceShown("dataset.export");
  const [selectedFormat, setFormat] = useState("csv");
  // Recheck on every render: an old advanced selection cannot escape the gate.
  const format = advancedFormats ? selectedFormat : "csv";
  const [versionId, setVersionId] = useState(null);
  const [rowsChoice, setRowsChoice] = useState("all");
  const [generated, setGenerated] = useState(true);
  const [rowMetadata, setRowMetadata] = useState(false);
  const [roles, setRoles] = useState(null);
  const [system, setSystem] = useState("");
  const [saving, setSaving] = useState(false);

  const hasFilter = Boolean(
    currentFilter && (currentFilter.filters?.length || currentFilter.search),
  );
  useEffect(() => {
    if (!open) return;
    // The grid's filter is what the person sees, so it is the default.
    setRowsChoice(hasFilter ? "filter" : "all");
    setRoles(null);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open || !versions.length) return;
    const wanted =
      versionNumber != null
        ? versions.find((v) => v.number === Number(versionNumber))
        : null;
    setVersionId((wanted ?? latest)?.id ?? null);
  }, [open, versions.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const version = versions.find((v) => v.id === versionId) ?? latest ?? null;
  const isLatest = !version || version.id === latest?.id;

  const splitsQ = useQuery({
    queryKey: ["dataset-export", "splits", datasetId],
    queryFn: () => axios.get(endpoints.develop.splits.list(datasetId)),
    select: (res) => res?.data?.result?.splits ?? [],
    enabled: open && Boolean(datasetId) && versioned,
    staleTime: 30 * 1000,
  });
  const splits = useMemo(
    () => (versioned ? splitsQ.data ?? [] : []),
    [versioned, splitsQ.data],
  );
  const splitCounts = useQueries({
    queries: splits.map((s) => ({
      queryKey: [
        "dataset-export",
        "count",
        datasetId,
        version?.id ?? "latest",
        s.id,
      ],
      queryFn: () =>
        countDatasetRows({ datasetId, version, isLatest, splitIds: [s.id] }),
      enabled: open && Boolean(version || isLatest),
      staleTime: 30 * 1000,
    })),
  });
  const filterCountQ = useQuery({
    queryKey: [
      "dataset-export",
      "filter-count",
      datasetId,
      version?.id ?? "latest",
      JSON.stringify(currentFilter ?? null),
    ],
    queryFn: () =>
      countDatasetRows({
        datasetId,
        version,
        isLatest,
        filters: currentFilter?.filters ?? [],
        search: currentFilter?.search ?? "",
      }),
    enabled: open && hasFilter,
    staleTime: 10 * 1000,
  });

  const splitId = rowsChoice.startsWith("split:") ? rowsChoice.slice(6) : null;
  const useFilter = rowsChoice === "filter" && hasFilter;
  const [progress, setProgress] = useState(null);
  const rowsQ = useQuery({
    queryKey: [
      "dataset-export",
      "rows",
      datasetId,
      version?.id ?? "latest",
      rowsChoice,
      useFilter ? JSON.stringify(currentFilter) : "",
    ],
    queryFn: () =>
      fetchDatasetRows({
        datasetId,
        version,
        isLatest,
        splitIds: splitId ? [splitId] : [],
        filters: useFilter ? currentFilter.filters ?? [] : null,
        search: useFilter ? currentFilter.search ?? "" : "",
        onProgress: (done, total) => setProgress({ done, total }),
      }),
    enabled:
      open &&
      Boolean(datasetId) &&
      (!versioned || versionsQ.isSuccess || versionsQ.isError),
    // A new export must re-authorize and read current values, not a prior file.
    staleTime: classic ? 0 : 10 * 1000,
    gcTime: classic ? 0 : 5 * 60 * 1000,
  });
  const data = rowsQ.data;
  const columns = useMemo(() => data?.columns ?? [], [data]);
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const datasetName = nameProp ?? data?.datasetName ?? "dataset";
  const userCols = useMemo(() => userColumnsOf(columns), [columns]);
  const genCols = useMemo(() => generatedColumnsOf(columns), [columns]);
  const metaKeys = useMemo(() => rowMetadataKeys(rows), [rows]);
  const autoRoles = useMemo(
    () => defaultMessageRoles(columns, rows),
    [columns, rows],
  );
  const msgRoles = roles ?? autoRoles;
  const blocker = formatBlocker("openai_ft", msgRoles);
  const blockers = { openai_ft: blocker, openai_evals: blocker };
  const isMessages = format === "openai_ft" || format === "openai_evals";
  const blocked = isMessages && Boolean(blocker);
  const generatedOn = generated && isLatest && genCols.length > 0;

  const splitNames = useMemo(
    () => Object.fromEntries(splits.map((s) => [s.id, s.name])),
    [splits],
  );
  const splitName = splitId ? splitNames[splitId] ?? splitId : null;
  const versionNo = version?.number ?? data?.version?.number ?? null;
  const versionTags = version?.tags;
  const file = useMemo(() => {
    if (!data || blocked) return null;
    const built = datasetRecords({
      columns,
      rows,
      splitNames,
      generated: generatedOn,
      rowMetadata,
    });
    return datasetFile({
      format,
      ...built,
      rows,
      columns,
      roles: msgRoles,
      system,
      meta: {
        dataset: datasetName,
        dataset_id: datasetId,
        dataset_version: versionNo,
        version_tags: versionTags ?? [],
        rows: splitName
          ? `split ${splitName}`
          : useFilter
            ? currentFilter?.label ?? "the current filter"
            : "all rows",
        filters: useFilter ? currentFilter?.filters ?? [] : [],
        generated_columns: generatedOn,
        row_count: rows.length,
        exported_at: new Date().toISOString(),
      },
      nameParts: [
        datasetName,
        versionNo != null ? `v${versionNo}` : null,
        splitName ?? (useFilter ? "filtered" : null),
      ],
    });
  }, [
    data,
    blocked,
    columns,
    rows,
    splitNames,
    format,
    generatedOn,
    rowMetadata,
    msgRoles,
    system,
    datasetName,
    datasetId,
    versionNo,
    versionTags,
    splitName,
    useFilter,
    currentFilter,
  ]);

  const lineCount = useMemo(() => {
    if (!isMessages) return rows.length;
    const byId = Object.fromEntries(columns.map((c) => [c.id, c]));
    return rows.filter((r) => {
      const flat = {};
      Object.values(msgRoles).forEach((id) => {
        if (id && byId[id]) flat[id] = r?.[id]?.cell_value ?? null;
      });
      return hasExpected(flat, {
        user: msgRoles.user,
        assistant: msgRoles.assistant,
        toolCalls: format === "openai_ft" ? msgRoles.toolCalls : undefined,
      });
    }).length;
  }, [isMessages, rows, columns, msgRoles, format]);
  const skipped = isMessages ? rows.length - lineCount : 0;

  const download = () => {
    if (!file) return;
    setSaving(true);
    try {
      const size = saveFile(file);
      notify(
        `Saved ${file.name} · ${fmtCount(lineCount, "row")} · ${sizeText(size)}`,
        {
          variant: "success",
          key: "dataset-export",
        },
      );
      onClose?.();
    } finally {
      setSaving(false);
    }
  };

  const total = latest?.row_count ?? null;
  const loading = rowsQ.isFetching && !rowsQ.data;
  const footer = rowsQ.isError ? (
    <Stack role="alert" gap={0.5} alignItems="flex-start">
      <Typography typography="s2" color="error.main">
        Couldn&apos;t read the rows. Try again.
      </Typography>
      <Button size="small" onClick={() => rowsQ.refetch()}>
        Retry
      </Button>
    </Stack>
  ) : loading || rowsQ.isFetching ? (
    <Stack gap={0.5}>
      <Typography typography="s2" color="text.secondary">
        {progress?.total
          ? `Reading rows ${progress.done} of ${progress.total}…`
          : "Reading rows…"}
      </Typography>
      <LinearProgress
        variant={progress?.total ? "determinate" : "indeterminate"}
        value={progress?.total ? (100 * progress.done) / progress.total : 0}
      />
    </Stack>
  ) : file ? (
    <Stack gap={0.25} sx={{ minWidth: 0 }}>
      <Typography typography="s2" data-testid="export-size">
        {`${fmtCount(lineCount, "row")} · about ${sizeText(byteLength(file.data))}`}
      </Typography>
      <Typography
        typography="s3"
        color="text.secondary"
        noWrap
        title={file.name}
        sx={{ fontFamily: "monospace" }}
        data-testid="export-file-name"
      >
        {file.name}
      </Typography>
    </Stack>
  ) : null;

  return (
    <Shell
      open={open}
      onClose={onClose}
      title={`Download “${datasetName}”`}
      footer={footer}
      busy={saving}
      onDownload={download}
      canDownload={
        Boolean(file) && !rowsQ.isError && !rowsQ.isFetching && lineCount > 0
      }
      testId="dataset-export-dialog"
    >
      <Box>
        <Typography sx={labelSx}>Format</Typography>
        <FormatList
          formats={
            advancedFormats ? DATASET_FORMATS : DATASET_FORMATS.slice(0, 1)
          }
          value={format}
          onChange={setFormat}
          blockers={data ? blockers : {}}
        />
      </Box>
      <Stack direction="row" gap={2}>
        {versioned && (
          <FormControl size="small" sx={{ flex: 1 }}>
            <Typography sx={labelSx}>Version</Typography>
            <Select
              value={version?.id ?? ""}
              onChange={(e) => {
                setVersionId(e.target.value);
                setRowsChoice((c) => (c === "filter" ? c : "all"));
              }}
              inputProps={{
                "aria-label": "Version",
                "data-testid": "export-version",
              }}
              sx={{ typography: "s2" }}
            >
              {versions.map((v) => (
                <MenuItem key={v.id} value={v.id}>
                  {versionLabel(v, latest)}
                  {v.row_count != null
                    ? ` · ${fmtCount(v.row_count, "row")}`
                    : ""}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}
        {classic ? (
          <Typography typography="s2">All current rows</Typography>
        ) : (
          <FormControl size="small" sx={{ flex: 1 }}>
            <Typography sx={labelSx}>Rows</Typography>
            <Select
              value={rowsChoice}
              onChange={(e) => setRowsChoice(e.target.value)}
              inputProps={{
                "aria-label": "Rows",
                "data-testid": "export-rows",
              }}
              sx={{ typography: "s2" }}
            >
              <MenuItem value="all">
                {`All ${version?.row_count ?? total ?? ""}`.trim()}
              </MenuItem>
              {splits.map((s, i) => {
                const n = splitCounts[i]?.data;
                if (n === 0) return null;
                return (
                  <MenuItem key={s.id} value={`split:${s.id}`}>
                    {`Split ${s.name}${n != null ? ` (${n})` : ""}`}
                  </MenuItem>
                );
              })}
              {hasFilter && (
                <MenuItem value="filter">
                  {/* HO D6: a selection names itself (`3 selected rows`). */}
                  {currentFilter?.label
                    ? currentFilter.label
                    : `The current filter${filterCountQ.data != null ? ` (${filterCountQ.data})` : ""}`}
                </MenuItem>
              )}
            </Select>
          </FormControl>
        )}
      </Stack>
      {isMessages ? (
        <Stack gap={1} data-testid="export-roles">
          <Typography sx={{ ...labelSx, mb: 0 }}>Messages</Typography>
          <RoleSelect
            label="User message"
            value={msgRoles.user}
            onChange={(id) => setRoles({ ...msgRoles, user: id })}
            columns={userCols}
            testId="export-role-user"
          />
          <RoleSelect
            label="Earlier turns"
            value={msgRoles.turns}
            optional
            onChange={(id) => setRoles({ ...msgRoles, turns: id })}
            columns={userCols}
            testId="export-role-turns"
          />
          <RoleSelect
            label="Assistant reply"
            value={msgRoles.assistant}
            onChange={(id) => setRoles({ ...msgRoles, assistant: id })}
            columns={userCols}
            testId="export-role-assistant"
          />
          {format === "openai_ft" && (
            <RoleSelect
              label="Tool calls"
              value={msgRoles.toolCalls}
              optional
              onChange={(id) => setRoles({ ...msgRoles, toolCalls: id })}
              columns={userCols}
              testId="export-role-tools"
            />
          )}
          <TextField
            size="small"
            label="System message (optional)"
            value={system}
            onChange={(e) => setSystem(e.target.value)}
            multiline
            maxRows={4}
            inputProps={{ "data-testid": "export-system" }}
          />
          {skipped > 0 && (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="export-skipped"
            >
              {`${fmtCount(skipped, "row")} without an expected value ${skipped === 1 ? "is" : "are"} left out`}
            </Typography>
          )}
        </Stack>
      ) : (
        <Stack gap={0.25}>
          {genCols.length > 0 && (
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={generatedOn}
                  disabled={!isLatest}
                  onChange={(e) => setGenerated(e.target.checked)}
                  inputProps={{ "data-testid": "export-generated" }}
                />
              }
              label={
                <Box>
                  <Typography typography="s1">
                    {`Generated columns (${genCols.map((c) => c.name).join(", ")})`}
                  </Typography>
                  {!isLatest && (
                    <Typography typography="s3" color="text.secondary">
                      Generated columns exist only on the latest version
                    </Typography>
                  )}
                </Box>
              }
            />
          )}
          {!isLatest && !genCols.length && (
            <Typography typography="s3" color="text.secondary">
              Generated columns exist only on the latest version
            </Typography>
          )}
          {metaKeys.length > 0 && (
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={rowMetadata}
                  onChange={(e) => setRowMetadata(e.target.checked)}
                  inputProps={{ "data-testid": "export-row-metadata" }}
                />
              }
              label={
                <Typography typography="s1">
                  {`Row metadata as columns (${metaKeys.join(", ")})`}
                </Typography>
              }
            />
          )}
        </Stack>
      )}
    </Shell>
  );
}

DatasetExportContent.propTypes = DatasetExportDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  datasetName: PropTypes.string,
  currentFilter: PropTypes.object,
  versionNumber: PropTypes.number,
};

/**
 * Results `Download…`: `(•) This view: 23 rows (…) ( ) Every row (248)`,
 * CSV / JSON / JSONL, one line per row (the grid) or per row and run.
 * @param {{open: boolean, onClose: Function, datasetId: string, view: object,
 *   name?: string}} props
 */
export function ResultsExportDialog({
  open,
  onClose,
  datasetId,
  view: pageView,
  name,
}) {
  const [scope, setScope] = useState("view");
  const [format, setFormat] = useState("csv");
  const [layout, setLayout] = useState("row");
  const [context, setContext] = useState(false);
  const [progress, setProgress] = useState(null);
  const [busy, setBusy] = useState(false);
  // A view read from the URL lacks the grid's search box: take it from the
  // grid's own live R3 read when the dialog opens. null = the grid's read
  // can't be matched, so there is no "This view" (only Every row).
  const queryClient = useQueryClient();
  const gridView = useMemo(() => {
    if (!open || pageView?.source !== "url") return pageView ?? null;
    const reads = queryClient
      .getQueryCache()
      .findAll({
        queryKey: [...runResultsKey(datasetId), "grid"],
        type: "active",
      })
      .map((q) => q.queryKey[q.queryKey.length - 1]);
    return viewFromGridReads(pageView, reads);
  }, [open, pageView, datasetId, queryClient]);
  const viewKnown = gridView != null;
  const view = gridView ?? pageView;
  const narrowed = viewKnown && viewIsNarrowed(view);
  useEffect(() => {
    if (open) setScope(narrowed ? "view" : "all");
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const multiRun = (view?.runIds?.length ?? 0) > 1;
  const url = endpoints.develop.datasetRuns.results(datasetId);
  const head = (v) => ({
    queryKey: [
      "results-export",
      "head",
      datasetId,
      runResultsParams({ ...v, sort: effectiveSort(v) }),
    ],
    queryFn: () =>
      axios.get(url, {
        params: {
          ...runResultsParams({ ...v, sort: effectiveSort(v) }),
          page: 0,
          page_size: 1,
        },
      }),
    select: (res) => res?.data?.result ?? null,
    enabled: open && Boolean(datasetId) && (v?.runIds?.length ?? 0) > 0,
    staleTime: 10 * 1000,
  });
  const viewHead = head(view);
  const viewQ = useQuery({
    ...viewHead,
    enabled: viewHead.enabled && narrowed,
  });
  const allQ = useQuery(head(everyRowView(view)));
  const summary = viewQ.data?.summary ?? allQ.data?.summary ?? null;
  const parts = viewParts(view, summary ?? {});
  const viewCount = viewQ.data?.total;
  const allCount = allQ.data?.total;
  const runLabels = (summary?.runs ?? []).map((r) => r.label).join(", ");
  const nameQ = useQuery({
    queryKey: ["results-export", "dataset-name", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), {
        params: { page_size: 1, current_page_index: 0 },
      }),
    select: (res) => res?.data?.result?.metadata?.dataset_name ?? null,
    enabled: open && Boolean(datasetId) && !name,
    staleTime: 60 * 1000,
  });
  const datasetName = name ?? nameQ.data ?? null;

  const download = async () => {
    setBusy(true);
    setProgress(null);
    try {
      const v = scope === "all" ? everyRowView(view) : view;
      const got = await fetchResultsExport(datasetId, v, {
        onProgress: setProgress,
      });
      const meta = resultsMeta({
        dataset: datasetName ?? datasetId,
        view: v,
        summary: got.summary,
        exportedAt: new Date().toISOString(),
        scope,
      });
      const built = resultsRecords({
        rows: got.rows,
        summary: got.summary,
        layout: multiRun ? layout : "row",
      });
      const file = resultsFile({
        format,
        ...built,
        meta,
        contextLine: format === "csv" && context,
        nameParts: [
          datasetName ?? "results",
          (got.summary?.runs ?? [])
            .map((r) => String(r.label ?? "").replace("#", ""))
            .join("-"),
          scope === "all" ? "all-rows" : narrowed ? "view" : null,
        ],
      });
      const size = saveFile(file);
      notify(
        `Saved ${file.name} · ${fmtCount(got.rows.length, "row")} · ${sizeText(size)}`,
        { variant: "success", key: "results-export" },
      );
      onClose?.();
    } catch {
      notify("Couldn't read the results. Try again.", {
        variant: "error",
        key: "results-export",
      });
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const footer = busy ? (
    <Stack gap={0.5}>
      <Typography typography="s2" color="text.secondary">
        {progress?.stage === "full"
          ? `Reading full rows ${progress.done} of ${progress.total}…`
          : "Reading rows…"}
      </Typography>
      <LinearProgress
        variant={progress?.total ? "determinate" : "indeterminate"}
        value={progress?.total ? (100 * progress.done) / progress.total : 0}
      />
    </Stack>
  ) : (
    <Typography
      typography="s2"
      color="text.secondary"
      data-testid="results-export-line"
    >
      {[
        (scope === "all" ? allCount : viewCount) != null &&
          fmtCount(scope === "all" ? allCount : viewCount, "row"),
        runLabels && `runs ${runLabels}`,
      ]
        .filter(Boolean)
        .join(" · ")}
    </Typography>
  );

  return (
    <Shell
      open={open}
      onClose={onClose}
      title="Download results"
      footer={footer}
      busy={busy}
      onDownload={download}
      canDownload={!busy && (scope === "all" ? allCount : viewCount) > 0}
      testId="results-export-dialog"
    >
      <Box>
        <Typography sx={labelSx}>Rows</Typography>
        <RadioGroup
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          aria-label="Rows"
        >
          {narrowed && (
            <FormControlLabel
              value="view"
              control={<Radio size="small" />}
              data-testid="results-export-scope-view"
              label={
                <Typography typography="s1">
                  {`This view: ${viewCount != null ? fmtCount(viewCount, "row") : "…"}${parts.length ? ` (${parts.join(" · ")})` : ""}`}
                </Typography>
              }
            />
          )}
          <FormControlLabel
            value="all"
            control={<Radio size="small" />}
            data-testid="results-export-scope-all"
            label={
              <Typography typography="s1">
                {`Every row${allCount != null ? ` (${allCount})` : ""}`}
              </Typography>
            }
          />
        </RadioGroup>
      </Box>
      <Box>
        <Typography sx={labelSx}>Format</Typography>
        <FormatList
          formats={RESULTS_FORMATS}
          value={format}
          onChange={setFormat}
        />
      </Box>
      {multiRun && (
        <Box>
          <Typography sx={labelSx}>Lines</Typography>
          <RadioGroup
            value={layout}
            onChange={(e) => setLayout(e.target.value)}
            aria-label="Lines"
          >
            <FormControlLabel
              value="row"
              control={<Radio size="small" />}
              label={
                <Typography typography="s1">
                  One line per row, like the grid
                </Typography>
              }
            />
            <FormControlLabel
              value="run"
              control={<Radio size="small" />}
              label={
                <Typography typography="s1">
                  One line per row and run, with a run column
                </Typography>
              }
            />
          </RadioGroup>
        </Box>
      )}
      {format === "csv" && (
        <FormControlLabel
          control={
            <Checkbox
              size="small"
              checked={context}
              onChange={(e) => setContext(e.target.checked)}
              inputProps={{ "data-testid": "results-export-context" }}
            />
          }
          label={
            <Typography typography="s1">
              Start with a # line naming the dataset, runs, baseline and filters
            </Typography>
          }
        />
      )}
      <Typography typography="s3" color="text.secondary">
        Each row carries its inputs, expected value, every run&apos;s output,
        eval values with the judge&apos;s reason, trials, latency, cost and
        trace ids, and human labels where the run has them.
      </Typography>
    </Shell>
  );
}

ResultsExportDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  view: PropTypes.object,
  name: PropTypes.string,
};

export default DatasetExportDialog;
