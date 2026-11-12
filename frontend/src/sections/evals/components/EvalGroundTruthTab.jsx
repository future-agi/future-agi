/* eslint-disable react/prop-types */
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  IconButton,
  LinearProgress,
  MenuItem,
  Slider,
  Step,
  StepLabel,
  Stepper,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import { useSnackbar } from "notistack";
import { AgGridReact } from "ag-grid-react";
import { useAgTheme } from "src/hooks/use-ag-theme";
import Iconify from "src/components/iconify";
import RunStatusBadge from "src/components/status-badge/StatusBadge";
import { apiPath } from "src/api/contracts/api-surface";
import axios from "src/utils/axios";

import {
  useDevelopDatasetList,
  useGetDatasetColumns,
} from "src/api/develop/develop-detail";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { useEvalDetail } from "../hooks/useEvalDetail";
import {
  useDeleteGroundTruth,
  useGroundTruthData,
  useGroundTruthList,
  useGroundTruthStatus,
  useSaveGroundTruthSetup,
  useTriggerEmbedding,
  useUploadGroundTruth,
} from "../hooks/useGroundTruth";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import {
  createEmptyGroundTruthDatasetRead,
  readNextGroundTruthDatasetPage,
} from "./ground_truth_dataset_pagination";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { ROW_HEIGHT } from "src/theme/sizes";
import ContentSkeleton from "src/components/content-skeleton";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import { JEV_GROUND_TRUTH_MESSAGE } from "../utils/jevMapping";

// The data endpoint returns at most 100 rows per page, so the preview pages
// on the server instead of loading every row into the grid.
const PREVIEW_PAGE_SIZE_OPTIONS = [25, 50, 100];
const PREVIEW_DEFAULT_PAGE_SIZE = 50;

// ═══════════════════════════════════════════════════════════════
// Status Badge
// ═══════════════════════════════════════════════════════════════
const StatusBadge = ({ status }) => {
  const map = {
    pending: {
      label: "Pending",
      role: "neutral",
      icon: "solar:clock-circle-linear",
    },
    processing: {
      label: "Embedding...",
      role: "info",
      icon: "mdi:loading",
    },
    completed: {
      label: "Ready",
      role: "success",
      icon: "solar:check-circle-linear",
    },
    failed: {
      label: "Failed",
      role: "danger",
      icon: "solar:danger-circle-linear",
    },
  };
  const info = map[status] || map.pending;
  return (
    <RunStatusBadge
      status={status}
      label={info.label}
      role={info.role}
      icon={
        <Iconify
          icon={info.icon}
          width={14}
          sx={
            status === "processing"
              ? {
                  animation: "spin 1s linear infinite",
                  "@keyframes spin": {
                    "100%": { transform: "rotate(360deg)" },
                  },
                }
              : {}
          }
        />
      }
    />
  );
};

// ═══════════════════════════════════════════════════════════════
// Upload Drawer - right sidebar like KB
// ═══════════════════════════════════════════════════════════════
const ACCEPTED_TYPES = {
  "text/csv": [".csv"],
  "application/json": [".json"],
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [
    ".xlsx",
  ],
  "application/vnd.ms-excel": [".xls"],
};

// ── shared helpers ───────────────────────────────────────────────
// Strip empty values + sort keys so two mappings compare structurally,
// independent of insertion order or trailing blanks. Used by every
// dirty-state check in this file.
const normalizeMapping = (m = {}) =>
  Object.fromEntries(
    Object.entries(m)
      .filter(([, v]) => v !== "" && v != null)
      .sort(([a], [b]) => a.localeCompare(b)),
  );

const formatRowCount = (value) => Number(value).toLocaleString();

const shallowEqual = (a, b) => {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((k) => a[k] === b[k]);
};

// Minimal RFC 4180 CSV parser: comma-separated, double-quote escaped fields
// (including embedded commas, newlines, and "" escapes). First row is the
// header. Returns { columns, rows }, where rows are dicts keyed by header.
const parseCsvText = (text) => {
  const src = text.replace(/^\uFEFF/, "");
  const records = [];
  let field = "";
  let row = [];
  let inQuotes = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (inQuotes) {
      if (c === '"') {
        if (src[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
      continue;
    }
    if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && src[i + 1] === "\n") i++;
      row.push(field);
      records.push(row);
      field = "";
      row = [];
    } else {
      field += c;
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    records.push(row);
  }
  const nonEmpty = records.filter((r) => !(r.length === 1 && r[0] === ""));
  if (nonEmpty.length === 0) return { columns: [], rows: [] };
  const [header, ...body] = nonEmpty;
  const columns = header.map((c) => c.trim());
  const rows = body.map((r) => {
    const obj = {};
    columns.forEach((col, idx) => {
      obj[col] = r[idx] ?? "";
    });
    return obj;
  });
  return { columns, rows };
};

// Explain a drop react-dropzone refused. String codes, not its ErrorCode
// export, so module mocks that only stub useDropzone keep working.
const describeRejectedDrop = (fileRejections) => {
  const errorsOf = (rejection) => rejection?.errors || [];
  const hasCode = (code) =>
    fileRejections.some((rejection) =>
      errorsOf(rejection).some((error) => error.code === code),
    );
  if (hasCode("file-invalid-type")) {
    return "Only .csv, .json, .xls or .xlsx files are supported.";
  }
  if (hasCode("file-too-large")) {
    const tooLarge = fileRejections.find((rejection) =>
      errorsOf(rejection).some((error) => error.code === "file-too-large"),
    );
    // Round up so a file just over the limit never reads as "50 MB".
    const sizeMb = Math.ceil((tooLarge.file.size / (1024 * 1024)) * 10) / 10;
    return `That file is ${sizeMb} MB. Ground truth files can be up to 50 MB.`;
  }
  if (hasCode("too-many-files")) return "Drop one file at a time.";
  return "That file can't be used. Choose one .csv, .json, .xls or .xlsx file up to 50 MB.";
};

const invalidJsonMessage = (err) => {
  const detail = String(err?.message || "")
    .trim()
    .replace(/\.$/, "");
  return detail
    ? `This file is not valid JSON: ${detail}. Fix the file and choose it again.`
    : "This file is not valid JSON. Fix the file and choose it again.";
};

export const UploadDrawer = ({
  open,
  onClose,
  templateId,
  evalVariables,
  canEdit = true,
}) => {
  const { enqueueSnackbar } = useSnackbar();
  const upload = useUploadGroundTruth(templateId);

  // Steps: 0 = choose source, 1 = configure (file), 2 = pick dataset, 3 = configure (dataset)
  const [step, setStep] = useState(0);
  const [file, setFile] = useState(null);
  const [name, setName] = useState("");
  const [variableMapping, setVariableMapping] = useState({});
  const [parsedColumns, setParsedColumns] = useState([]);
  const [dropError, setDropError] = useState("");
  const [jsonError, setJsonError] = useState("");
  // The file whose preview read may still update state; a later drop,
  // removal or reset makes an in-flight read stale.
  const previewFile = useRef(null);

  // Dataset selection state
  const [datasetSearch, setDatasetSearch] = useState("");
  const [selectedDataset, setSelectedDataset] = useState(null);
  // null | "rows" | "saving". Row loading can be canceled; saving cannot.
  const [datasetImportPhase, setDatasetImportPhase] = useState(null);
  const loadingDatasetData = datasetImportPhase !== null;
  const loadingDatasetRows = datasetImportPhase === "rows";
  const [datasetRead, setDatasetRead] = useState(() =>
    createEmptyGroundTruthDatasetRead(),
  );
  const [datasetReadError, setDatasetReadError] = useState("");
  const [datasetReadBlocked, setDatasetReadBlocked] = useState(false);
  const datasetReadGeneration = useRef(0);
  const datasetRowsAbort = useRef(null);

  // Fetch datasets list
  const { data: datasets = [], isLoading: datasetsLoading } =
    useDevelopDatasetList(datasetSearch, [], {}, {});

  // Fetch selected dataset's columns
  const _selectedDatasetId = selectedDataset?.dataset_id || selectedDataset?.id;
  const { data: datasetColumns } = useGetDatasetColumns(_selectedDatasetId, {
    enabled: !!_selectedDatasetId,
  });

  // Invalidate any running page loop and cancel its in-flight request. The
  // rows already loaded stay as an exact prefix that Import resumes from.
  const stopDatasetRowLoading = useCallback(() => {
    datasetReadGeneration.current += 1;
    datasetRowsAbort.current?.abort();
    datasetRowsAbort.current = null;
    setDatasetImportPhase(null);
  }, []);

  useEffect(() => {
    const generation = datasetReadGeneration;
    const rowsAbort = datasetRowsAbort;
    return () => {
      generation.current += 1;
      rowsAbort.current?.abort();
    };
  }, []);

  const reset = useCallback(() => {
    stopDatasetRowLoading();
    setStep(0);
    setFile(null);
    setName("");
    setVariableMapping({});
    setParsedColumns([]);
    setDropError("");
    setJsonError("");
    previewFile.current = null;
    setDatasetSearch("");
    setSelectedDataset(null);
    setDatasetRead(createEmptyGroundTruthDatasetRead());
    setDatasetReadError("");
    setDatasetReadBlocked(false);
  }, [stopDatasetRowLoading]);

  const handleClose = useCallback(() => {
    reset();
    onClose();
  }, [reset, onClose]);

  // ── File upload flow ──
  const onDrop = useCallback((accepted, fileRejections = []) => {
    if (!accepted.length) {
      if (fileRejections.length) {
        setDropError(describeRejectedDrop(fileRejections));
      }
      return;
    }
    const f = accepted[0];
    previewFile.current = f;
    setDropError("");
    setJsonError("");
    setParsedColumns([]);
    setFile(f);
    setName(f.name.replace(/\.(csv|xlsx?|json)$/i, ""));
    setStep(1);

    if (f.name.endsWith(".csv")) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const firstLine = e.target.result.split("\n")[0];
        const cols = firstLine
          .split(",")
          .map((c) => c.trim().replace(/^["']|["']$/g, ""));
        setParsedColumns(cols);
      };
      reader.readAsText(f.slice(0, 4096));
    } else if (f.name.endsWith(".json")) {
      const reader = new FileReader();
      reader.onload = (e) => {
        if (previewFile.current !== f) return;
        let parsed;
        try {
          parsed = JSON.parse(e.target.result);
        } catch (err) {
          setJsonError(invalidJsonMessage(err));
          return;
        }
        try {
          const arr = Array.isArray(parsed) ? parsed : parsed.data || [parsed];
          if (arr.length > 0) setParsedColumns(Object.keys(arr[0]));
        } catch {
          /* preview-only; a shape without row objects skips column detection */
        }
      };
      // Read the whole file: a prefix of valid JSON does not parse, so a
      // partial read cannot tell a large file from a malformed one.
      reader.readAsText(f);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive, isDragReject } =
    useDropzone({
      onDrop,
      accept: ACCEPTED_TYPES,
      multiple: false,
      maxSize: 50 * 1024 * 1024,
    });

  const handleFileUpload = useCallback(async () => {
    if (!file || !name) return;
    const lowerName = file.name.toLowerCase();
    const isCsv = lowerName.endsWith(".csv");
    const isJson = lowerName.endsWith(".json");
    try {
      let payload;
      if (isCsv || isJson) {
        // Parse client-side and send as JSON body so structured fields
        // (variable_mapping, role_mapping) travel as real objects rather
        // than JSON-encoded strings inside multipart.
        const text = await file.text();
        let columns;
        let data;
        if (isCsv) {
          const parsed = parseCsvText(text);
          columns = parsed.columns;
          data = parsed.rows;
        } else {
          let parsed;
          try {
            parsed = JSON.parse(text);
          } catch (parseErr) {
            const message = invalidJsonMessage(parseErr);
            setJsonError(message);
            enqueueSnackbar(message, { variant: "error" });
            return;
          }
          const arr = Array.isArray(parsed) ? parsed : parsed.data || [parsed];
          columns = arr.length > 0 ? Object.keys(arr[0]) : [];
          data = arr;
        }
        payload = { name, file_name: file.name, columns, data };
        if (Object.keys(variableMapping).length > 0) {
          payload.variable_mapping = variableMapping;
        }
      } else {
        // Binary uploads (xlsx/xls) still go via multipart; users set the
        // variable mapping post-upload from the GT setup screen.
        payload = new FormData();
        payload.append("file", file);
        payload.append("name", name);
      }
      await upload.mutateAsync(payload);
      enqueueSnackbar("Dataset uploaded successfully", { variant: "success" });
      handleClose();
    } catch (err) {
      // The axios interceptor rejects with the API error body ({ message,
      // result, code, statusCode }), so there is no err.response here. The
      // upload hook opts out of the global toast; this is the only one.
      enqueueSnackbar(
        getSafeActionErrorMessage(err, "Upload failed. Try again."),
        { variant: "error" },
      );
    }
  }, [file, name, variableMapping, upload, enqueueSnackbar, handleClose]);

  // ── Dataset selection flow ──
  const handleDatasetSelect = useCallback((ds) => {
    datasetReadGeneration.current += 1;
    setSelectedDataset(ds);
    setName(ds.name);
    setVariableMapping({});
    setDatasetRead(createEmptyGroundTruthDatasetRead());
    setDatasetReadError("");
    setDatasetReadBlocked(false);
    setDatasetImportPhase(null);
    setStep(3);
  }, []);

  // Derive column names from dataset columns response
  const datasetColumnNames = useMemo(() => {
    const columns = datasetRead.columns || datasetColumns;
    if (!columns) return [];
    return columns.map((col) => col.name || col.label || col.id || String(col));
  }, [datasetColumns, datasetRead.columns]);

  // The independent column-preview request is advisory. Once the exact
  // snapshot returns its signed inventory, remove mappings that do not exist
  // in that authoritative inventory so a dataset switch/race cannot retain a
  // stale column reference.
  useEffect(() => {
    if (!Array.isArray(datasetRead.columns)) return;
    const exactNames = new Set(
      datasetRead.columns
        .map((column) => String(column?.name || "").trim())
        .filter(Boolean),
    );
    setVariableMapping((previous) =>
      Object.fromEntries(
        Object.entries(previous).filter(
          ([, columnName]) => !columnName || exactNames.has(columnName),
        ),
      ),
    );
  }, [datasetRead.columns]);

  // Uploads a completely loaded exact read. The page loop below passes its
  // own final read, because component state is not re-rendered between the
  // last page and this call.
  const handleDatasetUpload = useCallback(
    async (read) => {
      setDatasetImportPhase("saving");
      try {
        if (!selectedDataset || !name || !read?.complete) return;
        const tableRows = read.rows;

        // Build the map from the exact paginated response rather than the
        // independently cached column query. Every page is required to carry
        // the same ordered column identity before it can be accumulated.
        const colMap = {};
        (read.columns || []).forEach((col) => {
          const colId = String(col.id || col.column_id);
          colMap[colId] = col.name || col.label || colId;
        });

        const colNames = Object.values(colMap);
        if (
          colNames.some((columnName) => !String(columnName).trim()) ||
          new Set(colNames).size !== colNames.length
        ) {
          enqueueSnackbar(
            "Dataset column names must be non-empty and unique before importing.",
            { variant: "error" },
          );
          return;
        }

        const invalidMappings = Object.entries(variableMapping).filter(
          ([, columnName]) => columnName && !colNames.includes(columnName),
        );
        if (invalidMappings.length > 0) {
          enqueueSnackbar(
            "Variable mappings no longer match the exact dataset columns. Review them before importing.",
            { variant: "error" },
          );
          return;
        }
        let flatRows = [];

        // table rows are: {column_uuid: {cell_value, ...}, row_id: "..."}
        if (tableRows.length > 0) {
          flatRows = tableRows.map((row) => {
            const obj = {};
            Object.entries(row).forEach(([colId, cellData]) => {
              if (colId === "row_id") return;
              const colName = colMap[colId];
              if (colName && cellData) {
                obj[colName] =
                  typeof cellData === "object"
                    ? cellData.cell_value ?? cellData.value ?? ""
                    : cellData;
              }
            });
            return obj;
          });
        }

        if (flatRows.length === 0) {
          enqueueSnackbar("Dataset has no rows", { variant: "warning" });
          return;
        }

        // Upload as JSON body
        const payload = {
          name,
          file_name: `${selectedDataset.name}.json`,
          columns: colNames,
          data: flatRows,
        };
        if (Object.keys(variableMapping).length > 0) {
          payload.variable_mapping = variableMapping;
        }

        await upload.mutateAsync(payload);
        enqueueSnackbar(
          `Imported ${flatRows.length} rows from "${selectedDataset.name}"`,
          { variant: "success" },
        );
        handleClose();
      } catch (err) {
        enqueueSnackbar(
          getSafeActionErrorMessage(err, "Import failed. Try again."),
          { variant: "error" },
        );
      } finally {
        setDatasetImportPhase(null);
      }
    },
    [
      selectedDataset,
      name,
      variableMapping,
      enqueueSnackbar,
      handleClose,
      upload,
    ],
  );

  // One Import click loads every remaining page in sequence, then imports.
  // Each page is still one bounded request with the exact per-page checks.
  // The first failure stops the loop and keeps the loaded rows, so the next
  // click resumes from the first row that is not loaded yet.
  const handleDatasetImport = useCallback(async () => {
    if (!selectedDataset || !name || loadingDatasetData || datasetReadBlocked)
      return;
    const datasetId = selectedDataset.dataset_id || selectedDataset.id;
    datasetReadGeneration.current += 1;
    const readGeneration = datasetReadGeneration.current;
    const rowsAbort = new AbortController();
    datasetRowsAbort.current = rowsAbort;
    let read = datasetRead;
    setDatasetImportPhase("rows");
    setDatasetReadError("");
    try {
      while (!read.complete) {
        read = await readNextGroundTruthDatasetPage({
          previous: read,
          signal: rowsAbort.signal,
          requestPage: ({ pageIndex, pageSize, cursor, signal, timeout }) =>
            axios.get(
              apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
                dataset_id: datasetId,
              }),
              {
                params: {
                  current_page_index: pageIndex,
                  page_size: pageSize,
                  exact_snapshot: true,
                  ...(cursor ? { cursor } : {}),
                },
                signal,
                timeout,
              },
            ),
        });
        if (datasetReadGeneration.current !== readGeneration) return;
        setDatasetRead(read);
      }
    } catch (err) {
      if (datasetReadGeneration.current !== readGeneration) return;
      const message =
        err?.response?.data?.message ||
        err?.message ||
        "Dataset rows could not be loaded exactly. Retry.";
      setDatasetReadError(message);
      setDatasetReadBlocked(
        err?.code === "dataset_exact_limit_exceeded" ||
          err?.response?.data?.code === "dataset_exact_limit_exceeded",
      );
      setDatasetImportPhase(null);
      enqueueSnackbar(message, { variant: "error" });
      return;
    } finally {
      if (datasetRowsAbort.current === rowsAbort) {
        datasetRowsAbort.current = null;
      }
    }
    if (read.totalRows === 0) {
      setDatasetImportPhase(null);
      enqueueSnackbar("Dataset has no rows", { variant: "warning" });
      return;
    }
    await handleDatasetUpload(read);
  }, [
    datasetRead,
    datasetReadBlocked,
    enqueueSnackbar,
    handleDatasetUpload,
    loadingDatasetData,
    name,
    selectedDataset,
  ]);

  // Columns for the mapping step (from file or from dataset)
  const activeColumns = step === 1 ? parsedColumns : datasetColumnNames;
  const isConfigStep = step === 1 || step === 3;
  const isSubmitting = upload.isPending || loadingDatasetData;
  const covered = useDrawerCovered(open);

  const datasetRowsProgress =
    datasetRead.totalRows === null
      ? null
      : `${formatRowCount(datasetRead.rows.length)} of ${formatRowCount(
          datasetRead.totalRows,
        )} rows`;
  const datasetRowsCaption =
    datasetRowsProgress === null
      ? loadingDatasetRows
        ? "Loading rows..."
        : "Import loads every row in this dataset."
      : loadingDatasetRows
        ? `Loading ${datasetRowsProgress}...`
        : `${datasetRowsProgress} loaded`;
  const datasetImportLabel =
    datasetReadError && !datasetReadBlocked && !datasetRead.complete
      ? datasetRead.rows.length > 0
        ? `Retry from row ${formatRowCount(datasetRead.rows.length + 1)}`
        : "Retry"
      : "Import";

  return (
    <Drawer
      anchor="right"
      open={open}
      sx={coveredDrawerSx(covered)}
      onClose={handleClose}
      PaperProps={{
        sx: {
          width: 480,
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "12px 0 0 12px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: { style: { backgroundColor: "rgba(0,0,0,0.3)" } },
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "100%",
          p: 2.5,
        }}
      >
        {/* Header */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            mb: 2,
          }}
        >
          <Typography variant="subtitle1" fontWeight={600}>
            {step === 0
              ? "Add Ground Truth"
              : step === 2
                ? "Choose Dataset"
                : "Configure Dataset"}
          </Typography>
          <IconButton size="small" aria-label="Close" onClick={handleClose}>
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Box>

        {/* Steps indicator */}
        <Stepper
          activeStep={step === 2 ? 0 : isConfigStep ? 1 : 0}
          alternativeLabel
          sx={{ mb: 3, "& .MuiStepLabel-label": { fontSize: "12px" } }}
        >
          <Step>
            <StepLabel>Choose Source</StepLabel>
          </Step>
          <Step>
            <StepLabel>Map Variables</StepLabel>
          </Step>
        </Stepper>

        {/* ═══ Step 0: Choose source ═══ */}
        {step === 0 && (
          <Box
            sx={{ display: "flex", flexDirection: "column", gap: 2, flex: 1 }}
          >
            {/* Upload file */}
            <Box
              {...getRootProps()}
              sx={{
                border: "2px dashed",
                borderColor:
                  isDragReject || (dropError && !isDragActive)
                    ? "error.main"
                    : isDragActive
                      ? "primary.main"
                      : "divider",
                borderRadius: "8px",
                p: 4,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 1.5,
                cursor: "pointer",
                transition: "all 0.2s",
                bgcolor: isDragActive ? "status.discovery.bg" : "transparent",
                "&:hover": {
                  borderColor: "primary.main",
                  bgcolor: "background.neutral",
                },
              }}
            >
              <input {...getInputProps()} />
              <Box
                sx={{
                  width: 48,
                  height: 48,
                  borderRadius: "8px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  bgcolor: "status.discovery.bg",
                }}
              >
                <Iconify
                  icon="solar:cloud-upload-linear"
                  width={24}
                  sx={{ color: "primary.main" }}
                />
              </Box>
              <Typography variant="body2" fontWeight={500}>
                {isDragActive
                  ? "Drop file here"
                  : "Choose a file or drag & drop"}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                textAlign="center"
              >
                CSV, Excel (.xls, .xlsx), or JSON. Up to 50 MB.
              </Typography>
              <Button
                variant="outlined"
                size="small"
                sx={{
                  mt: 0.5,
                  borderColor: "divider",
                  color: "text.primary",
                }}
              >
                Browse files
              </Button>
            </Box>
            {dropError && (
              <Typography
                role="alert"
                variant="caption"
                color="status.danger.fg"
              >
                {dropError}
              </Typography>
            )}

            <Divider sx={{ my: 0.5 }}>
              <Typography variant="caption" color="text.disabled">
                or
              </Typography>
            </Divider>

            {/* From existing dataset */}
            <ButtonBase
              component="div"
              onClick={() => setStep(2)}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
                p: 2.5,
                width: "100%",
                display: "flex",
                justifyContent: "flex-start",
                alignItems: "center",
                textAlign: "left",
                gap: 2,
                cursor: "pointer",
                transition: "all 0.2s",
                "&:hover": {
                  borderColor: "primary.main",
                  bgcolor: "background.neutral",
                },
              }}
            >
              <Box
                sx={{
                  width: 40,
                  height: 40,
                  borderRadius: "8px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  bgcolor: "background.neutral",
                }}
              >
                <Iconify
                  icon="solar:database-linear"
                  width={20}
                  sx={{ color: "text.secondary" }}
                />
              </Box>
              <Box sx={{ flex: 1 }}>
                <Typography variant="body2" fontWeight={500}>
                  Choose from existing dataset
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Select from your uploaded datasets
                </Typography>
              </Box>
              <Iconify
                icon="solar:alt-arrow-right-linear"
                width={20}
                sx={{ color: "text.disabled" }}
              />
            </ButtonBase>
          </Box>
        )}

        {/* ═══ Step 2: Dataset picker ═══ */}
        {step === 2 && (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 1.5,
              flex: 1,
              minHeight: 0,
            }}
          >
            <TextField
              size="small"
              placeholder="Search datasets..."
              value={datasetSearch}
              onChange={(e) => setDatasetSearch(e.target.value)}
              InputProps={{
                startAdornment: (
                  <Iconify
                    icon="solar:magnifier-linear"
                    width={16}
                    sx={{ mr: 0.5, color: "text.disabled" }}
                  />
                ),
              }}
              sx={{ "& .MuiInputBase-input": { fontSize: "12px" } }}
            />

            <Box
              sx={{
                flex: 1,
                overflow: "auto",
                display: "flex",
                flexDirection: "column",
                gap: 1,
              }}
            >
              {datasetsLoading && (
                <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
                  <CircularProgress size={20} />
                </Box>
              )}

              {!datasetsLoading && datasets.length === 0 && (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  textAlign="center"
                  sx={{ py: 4 }}
                >
                  No datasets found
                </Typography>
              )}

              {datasets.map((ds) => (
                <ButtonBase
                  component="div"
                  key={ds.dataset_id || ds.id}
                  onClick={() => handleDatasetSelect(ds)}
                  sx={{
                    p: 1.5,
                    borderRadius: "8px",
                    border: "1px solid",
                    borderColor: "divider",
                    cursor: "pointer",
                    width: "100%",
                    display: "flex",
                    justifyContent: "flex-start",
                    alignItems: "center",
                    textAlign: "left",
                    gap: 1.5,
                    transition: "all 0.15s",
                    "&:hover": {
                      borderColor: "primary.main",
                      bgcolor: "background.neutral",
                    },
                  }}
                >
                  <Iconify
                    icon="solar:table-linear"
                    width={20}
                    sx={{ color: "primary.main", flexShrink: 0 }}
                  />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={500} noWrap>
                      {ds.name}
                    </Typography>
                    {ds.row_count != null && (
                      <Typography variant="caption" color="text.secondary">
                        {ds.row_count} rows
                      </Typography>
                    )}
                  </Box>
                  <Iconify
                    icon="solar:alt-arrow-right-linear"
                    width={20}
                    sx={{ color: "text.disabled", flexShrink: 0 }}
                  />
                </ButtonBase>
              ))}
            </Box>

            {/* Back button */}
            <Box
              sx={{ pt: 1.5, borderTop: "1px solid", borderColor: "divider" }}
            >
              <Button
                variant="outlined"
                size="small"
                onClick={() => setStep(0)}
                fullWidth
              >
                Back
              </Button>
            </Box>
          </Box>
        )}

        {/* ═══ Step 1/3: Configure (file or dataset) ═══ */}
        {isConfigStep && (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 2.5,
              flex: 1,
              overflow: "auto",
            }}
          >
            {/* Source info */}
            {step === 1 && file && (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1.5,
                  p: 1.5,
                  borderRadius: "8px",
                  border: "1px solid",
                  borderColor: jsonError ? "error.main" : "divider",
                }}
              >
                <Iconify
                  icon={
                    file.name.endsWith(".csv")
                      ? "mdi:file-delimited-outline"
                      : file.name.endsWith(".json")
                        ? "solar:code-linear"
                        : "mdi:file-excel-outline"
                  }
                  width={20}
                  sx={{ color: "primary.main", flexShrink: 0 }}
                />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" noWrap fontWeight={500}>
                    {file.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {(file.size / 1024).toFixed(0)} KB
                  </Typography>
                </Box>
                <IconButton
                  size="small"
                  aria-label="Remove file"
                  onClick={() => {
                    previewFile.current = null;
                    setFile(null);
                    setStep(0);
                    setParsedColumns([]);
                    setJsonError("");
                  }}
                >
                  <Iconify icon="solar:close-linear" width={16} />
                </IconButton>
              </Box>
            )}
            {step === 1 && jsonError && (
              <Typography
                role="alert"
                variant="caption"
                color="status.danger.fg"
              >
                {jsonError}
              </Typography>
            )}

            {step === 3 && selectedDataset && (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1.5,
                  p: 1.5,
                  borderRadius: "8px",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Iconify
                  icon="solar:table-linear"
                  width={20}
                  sx={{ color: "primary.main", flexShrink: 0 }}
                />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" noWrap fontWeight={500}>
                    {selectedDataset.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {selectedDataset.row_count != null
                      ? `${selectedDataset.row_count} rows`
                      : "Existing dataset"}
                  </Typography>
                </Box>
                <IconButton
                  size="small"
                  aria-label="Choose a different dataset"
                  onClick={() => {
                    stopDatasetRowLoading();
                    setSelectedDataset(null);
                    setDatasetRead(createEmptyGroundTruthDatasetRead());
                    setDatasetReadError("");
                    setDatasetReadBlocked(false);
                    setStep(2);
                  }}
                >
                  <Iconify icon="solar:close-linear" width={16} />
                </IconButton>
              </Box>
            )}

            {step === 3 && selectedDataset && (
              <Box
                sx={{
                  p: 1.5,
                  borderRadius: "8px",
                  border: "1px solid",
                  borderColor: datasetReadError ? "error.main" : "divider",
                  display: "flex",
                  flexDirection: "column",
                  gap: 1,
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 1,
                  }}
                >
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={600}>
                      Dataset rows
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {datasetRowsCaption}
                    </Typography>
                  </Box>
                  {datasetRead.complete && (
                    <RunStatusBadge
                      status="completed"
                      label="Complete"
                      role="success"
                    />
                  )}
                </Box>
                {datasetRead.totalRows === null && loadingDatasetRows && (
                  <LinearProgress />
                )}
                {datasetRead.totalRows !== null &&
                  datasetRead.totalRows > 0 && (
                    <LinearProgress
                      variant="determinate"
                      value={Math.min(
                        100,
                        (datasetRead.rows.length / datasetRead.totalRows) * 100,
                      )}
                    />
                  )}
                {datasetReadError && (
                  <Typography variant="caption" color="status.danger.fg">
                    {datasetReadError}
                  </Typography>
                )}
                {datasetRead.rows.length > 0 &&
                  !datasetRead.complete &&
                  !datasetReadBlocked && (
                    <Button
                      variant="text"
                      color="inherit"
                      size="small"
                      disabled={loadingDatasetData}
                      onClick={() => {
                        datasetReadGeneration.current += 1;
                        setDatasetRead(createEmptyGroundTruthDatasetRead());
                        setDatasetReadError("");
                        setDatasetReadBlocked(false);
                      }}
                      sx={{ alignSelf: "flex-start", px: 0 }}
                    >
                      Restart row loading
                    </Button>
                  )}
              </Box>
            )}

            {/* Dataset name */}
            <TextField
              size="small"
              label="Ground truth name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={step === 3 && loadingDatasetData}
              fullWidth
              sx={{ "& .MuiInputBase-input": { fontSize: "12px" } }}
            />

            {/* Variable mapping */}
            {evalVariables.length > 0 && activeColumns.length > 0 && (
              <>
                <Divider />
                <Box>
                  <Typography variant="body2" fontWeight={600} sx={{ mb: 0.5 }}>
                    Map Variables
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ mb: 1.5, display: "block" }}
                  >
                    Map eval template variables to columns in your dataset
                  </Typography>
                  {evalVariables.map((varName) => (
                    <Box
                      key={varName}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        mb: 1.5,
                      }}
                    >
                      <Tooltip title={varName} placement="top" arrow>
                        <Chip
                          label={varName}
                          size="small"
                          variant="outlined"
                          sx={{
                            minWidth: 100,
                            maxWidth: 200,
                            flexShrink: 0,
                            fontFamily: "monospace",
                          }}
                        />
                      </Tooltip>
                      <Iconify
                        icon="solar:arrow-right-linear"
                        width={14}
                        sx={{ color: "text.disabled", flexShrink: 0 }}
                      />
                      <TextField
                        select
                        size="small"
                        fullWidth
                        value={variableMapping[varName] || ""}
                        disabled={step === 3 && loadingDatasetData}
                        onChange={(e) =>
                          setVariableMapping((prev) => ({
                            ...prev,
                            [varName]: e.target.value,
                          }))
                        }
                        sx={{ "& .MuiInputBase-input": { fontSize: "12px" } }}
                      >
                        <MenuItem value="">
                          <em>Skip</em>
                        </MenuItem>
                        {activeColumns.map((col) => (
                          <MenuItem key={col} value={col}>
                            {col}
                          </MenuItem>
                        ))}
                      </TextField>
                    </Box>
                  ))}
                </Box>
              </>
            )}

            {/* Columns preview */}
            {activeColumns.length > 0 && (
              <Box>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ mb: 0.5, display: "block" }}
                >
                  {step === 1 ? "Detected" : "Dataset"} columns (
                  {activeColumns.length})
                </Typography>
                <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
                  {activeColumns.map((col) => (
                    <Chip
                      key={col}
                      label={col}
                      size="small"
                      variant="outlined"
                    />
                  ))}
                </Box>
              </Box>
            )}
          </Box>
        )}

        {/* Footer */}
        {isConfigStep && (
          <Box
            sx={{
              display: "flex",
              gap: 1,
              pt: 2,
              borderTop: "1px solid",
              borderColor: "divider",
              mt: "auto",
            }}
          >
            <Button
              variant="outlined"
              size="small"
              onClick={
                loadingDatasetRows
                  ? stopDatasetRowLoading
                  : () => setStep(step === 3 ? 2 : 0)
              }
              sx={{ flex: 1 }}
            >
              {loadingDatasetRows ? "Cancel" : "Back"}
            </Button>
            <Button
              variant="contained"
              size="small"
              onClick={step === 1 ? handleFileUpload : handleDatasetImport}
              disabled={
                !canEdit ||
                (step === 1 && (!file || !name)) ||
                (step === 1 && Boolean(jsonError)) ||
                (step === 3 && (!selectedDataset || !name)) ||
                (step === 3 &&
                  datasetRead.complete &&
                  datasetRead.totalRows === 0) ||
                (step === 3 && datasetReadBlocked) ||
                isSubmitting
              }
              sx={{ flex: 1 }}
            >
              {isSubmitting ? (
                <CircularProgress size={16} sx={{ color: "inherit" }} />
              ) : step === 3 ? (
                datasetImportLabel
              ) : (
                "Upload"
              )}
            </Button>
          </Box>
        )}
      </Box>
    </Drawer>
  );
};

// ═══════════════════════════════════════════════════════════════
// Empty state
// ═══════════════════════════════════════════════════════════════
const EmptyState = ({ onUpload, canEdit = true }) => (
  <Box
    onClick={canEdit ? onUpload : undefined}
    sx={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      py: 8,
      gap: 2,
      height: "100%",
      cursor: canEdit ? "pointer" : "not-allowed",
      opacity: canEdit ? 1 : 0.6,
      borderRadius: "12px",
      border: "1px dashed",
      borderColor: "divider",
      transition: "all 0.2s",
      "&:hover": {
        borderColor: "primary.main",
        bgcolor: "background.neutral",
      },
    }}
  >
    <Box
      sx={{
        width: 56,
        height: 56,
        borderRadius: "12px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: "background.neutral",
      }}
    >
      <Iconify
        icon="solar:add-folder-linear"
        width={28}
        sx={{ color: "text.disabled" }}
      />
    </Box>
    <Typography variant="subtitle1" fontWeight={600}>
      Add ground truth dataset
    </Typography>
    <Typography
      variant="body2"
      color="text.secondary"
      textAlign="center"
      maxWidth={400}
    >
      Upload annotated data to calibrate evaluations with human-scored reference
      examples.
    </Typography>
    <Button
      variant="contained"
      size="small"
      disabled={!canEdit}
      onClick={(event) => {
        // The surrounding area opens the same drawer; open it only once.
        event.stopPropagation();
        onUpload();
      }}
    >
      Add ground truth
    </Button>
    <Typography variant="caption" color="text.secondary">
      Upload a file or choose a dataset
    </Typography>
  </Box>
);

// Flat single-save form for the GT tab. Posts to
// /ground-truth/<id>/setup/, which rejects when the mandatory `output`
// column is missing. The output-type hint reads
// `template.output_type_normalized` + `choice_scores`; column content
// is not validated against the type.

const OUTPUT_TYPE_HINTS = {
  pass_fail: "Use Pass / Fail / True / False / Yes / No.",
  percentage: "Use a number between 0 and 1.",
};

export function shouldTriggerEmbed({
  enabled,
  mappingDirty,
  embeddingsReady,
  hasOnEmbed,
}) {
  return Boolean(enabled && (mappingDirty || !embeddingsReady) && hasOnEmbed);
}

function describeReferenceOutputColumn(evalConfig) {
  if (!evalConfig) {
    return "The correct answer for each row.";
  }
  const outputType =
    evalConfig.output_type_normalized ||
    evalConfig.outputTypeNormalized ||
    (evalConfig.output || "").toLowerCase();
  const choiceScores =
    evalConfig.choice_scores || evalConfig.choiceScores || null;
  const choiceLabels =
    choiceScores && typeof choiceScores === "object"
      ? Object.keys(choiceScores)
      : null;

  if (choiceLabels && choiceLabels.length > 0) {
    return `Use one of: ${choiceLabels.join(", ")}.`;
  }
  if (outputType && OUTPUT_TYPE_HINTS[outputType]) {
    return OUTPUT_TYPE_HINTS[outputType];
  }
  if (outputType === "deterministic") {
    return "Use one of the eval's configured choices.";
  }
  return "Match the format of your eval's output.";
}

const GroundTruthSetupForm = ({
  gt,
  template,
  evalVariables,
  rulePrompt,
  onEmbed,
  onTest,
  embedPending,
  embeddingStatus,
  embeddingsStale,
  isJevModel = false,
  canEdit = true,
}) => {
  // Always derive Jinja variables from the live rule prompt so removing
  // a variable from the prompt also drops its row from the mapping UI
  // even before the eval config field is repopulated.
  const liveVariables = useMemo(() => {
    if (evalVariables && evalVariables.length > 0) return evalVariables;
    return extractJinjaVariables(rulePrompt || "");
  }, [evalVariables, rulePrompt]);

  const persistedVarMapping = useMemo(
    () => normalizeMapping(gt.variable_mapping || {}),
    [gt.variable_mapping],
  );
  const persistedRoleMapping = useMemo(
    () => gt.role_mapping || {},
    [gt.role_mapping],
  );

  const initialOutputColumn =
    persistedRoleMapping.output || persistedRoleMapping.expected_output || "";
  const initialExplanationColumn =
    persistedRoleMapping.explanation ||
    persistedRoleMapping.reasoning ||
    persistedRoleMapping.reason ||
    "";

  const templateConfig = template?.config || {};
  // Runtime knobs live on the GT row itself; the previous template.config
  // snapshot was removed in favour of per-tenant typed columns.
  const persistedMaxExamples = gt.max_examples ?? gt.maxExamples ?? 3;
  const persistedEnabled = Boolean(gt.enabled);

  const [varMapping, setVarMapping] = useState(persistedVarMapping);
  const [outputColumn, setOutputColumn] = useState(initialOutputColumn);
  const [explanationColumn, setExplanationColumn] = useState(
    initialExplanationColumn,
  );
  const [maxExamples, setMaxExamples] = useState(persistedMaxExamples);
  const [enabled, setEnabled] = useState(persistedEnabled);

  // Resync local state whenever the persisted snapshot changes (post-save
  // refetch). This keeps the dirty indicator honest.
  useEffect(() => setVarMapping(persistedVarMapping), [persistedVarMapping]);
  useEffect(() => setOutputColumn(initialOutputColumn), [initialOutputColumn]);
  useEffect(
    () => setExplanationColumn(initialExplanationColumn),
    [initialExplanationColumn],
  );
  useEffect(() => setMaxExamples(persistedMaxExamples), [persistedMaxExamples]);
  useEffect(() => setEnabled(persistedEnabled), [persistedEnabled]);

  const save = useSaveGroundTruthSetup(template?.id);
  const [embedChainRunning, setEmbedChainRunning] = useState(false);

  useEffect(() => {
    if (!embedChainRunning) return;
    if (embeddingStatus === "completed" || embeddingStatus === "failed") {
      setEmbedChainRunning(false);
    }
  }, [embedChainRunning, embeddingStatus]);

  useEffect(() => {
    if (save.isError) setEmbedChainRunning(false);
  }, [save.isError]);

  const mappingDirty = !shallowEqual(
    normalizeMapping(varMapping),
    persistedVarMapping,
  );
  const paramsDirty =
    outputColumn !== initialOutputColumn ||
    explanationColumn !== initialExplanationColumn ||
    Number(maxExamples) !== Number(persistedMaxExamples) ||
    enabled !== persistedEnabled;

  const hasMapping = Object.keys(normalizeMapping(varMapping)).length > 0;
  const canSave =
    Boolean(outputColumn) &&
    (!enabled || (hasMapping && !isJevModel)) &&
    !save.isPending;
  const embedActive =
    embedChainRunning ||
    embedPending ||
    embeddingStatus === "processing" ||
    (embeddingStatus === "pending" && embedPending);
  const embeddingsReady = embeddingStatus === "completed" && !embeddingsStale;

  const configDirty = mappingDirty || paramsDirty;
  // Embed only matters when GT is enabled; a paused GT should settle to
  // "Saved" without pushing the user toward embedding.
  const needsEmbed = enabled && !embeddingsReady && !embedActive;
  const hasWork = configDirty || needsEmbed;
  const ctaPending = save.isPending || embedActive;
  let ctaLabel;
  if (save.isPending) ctaLabel = "Saving…";
  else if (embedActive) ctaLabel = "Embedding…";
  else if (hasWork) ctaLabel = "Save";
  else ctaLabel = "Saved";
  const ctaDisabled = !canEdit || !canSave || ctaPending || !hasWork;

  const buildPayload = () => {
    const role = { output: outputColumn };
    if (explanationColumn) role.explanation = explanationColumn;
    return {
      gtId: gt.id,
      variableMapping: normalizeMapping(varMapping),
      roleMapping: role,
      maxExamples: Number(maxExamples),
      enabled,
    };
  };

  const handleCtaClick = async () => {
    if (enabled && isJevModel) return;
    try {
      if (configDirty) await save.mutateAsync(buildPayload());
      if (
        shouldTriggerEmbed({
          enabled,
          mappingDirty,
          embeddingsReady,
          hasOnEmbed: !!onEmbed,
        })
      ) {
        setEmbedChainRunning(true);
        await onEmbed();
      }
    } catch (err) {
      setEmbedChainRunning(false);
      throw err;
    }
  };

  const columns = gt.columns || [];
  const referenceHint = describeReferenceOutputColumn(templateConfig);

  // Small reusable card surface so every section reads as a distinct
  // group instead of one undifferentiated stack of rows.
  const sectionCardSx = {
    border: 1,
    borderColor: "divider",
    borderRadius: 1,
    p: 1.5,
    display: "flex",
    flexDirection: "column",
    gap: 1.25,
    bgcolor: "background.paper",
  };
  const sectionTitleSx = {
    fontSize: "12px",
    fontWeight: 600,
    color: "text.primary",
  };
  const labelColSx = {
    width: 130,
    flexShrink: 0,
    color: "text.secondary",
    fontSize: "12px",
  };
  const fieldFontSx = { "& .MuiInputBase-input": { fontSize: "12px" } };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {/* Header toggle. Drives gt.enabled, which the runtime checks
          before injecting GT context. */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          p: 1.25,
          border: 1,
          borderColor: enabled ? "primary.main" : "divider",
          borderRadius: 1,
          bgcolor: enabled ? "action.hover" : "background.paper",
          width: "100%",
          minWidth: 0,
          boxSizing: "border-box",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            flex: 1,
            minWidth: 0,
          }}
        >
          <Typography
            variant="body2"
            sx={{
              fontWeight: 600,
              fontSize: "12px",
              lineHeight: 1.3,
              wordBreak: "break-word",
            }}
          >
            Use ground truth
          </Typography>
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              fontSize: "12px",
              lineHeight: 1.3,
              wordBreak: "break-word",
            }}
          >
            {isJevModel
              ? "Jev cannot use these examples. Turn off ground truth to save and run."
              : enabled
                ? "On. Retrieved examples are injected into the evaluator prompt."
                : "Off. The evaluator runs without ground truth context."}
          </Typography>
        </Box>
        <Switch
          size="small"
          checked={enabled}
          disabled={isJevModel && !enabled}
          onChange={(_, v) => setEnabled(v)}
          sx={{ flexShrink: 0, ml: 1 }}
        />
      </Box>

      {/* One row per template variable. */}
      <Box sx={sectionCardSx}>
        <Typography sx={sectionTitleSx}>Inputs</Typography>
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", fontSize: "12px", mt: -0.5 }}
        >
          Map each template variable to a ground truth column.
        </Typography>
        {enabled && !hasMapping && liveVariables.length > 0 ? (
          <Typography
            variant="caption"
            sx={{ color: "status.danger.fg", fontSize: "12px", mt: 0.25 }}
          >
            Map at least one input variable before saving with ground truth
            enabled.
          </Typography>
        ) : null}
        {liveVariables.length === 0 ? (
          <Typography variant="caption" color="text.secondary">
            This rule prompt has no <code>{"{{variables}}"}</code> to map.
          </Typography>
        ) : (
          liveVariables.map((varName) => (
            <Box
              key={varName}
              sx={{ display: "flex", alignItems: "center", gap: 1 }}
            >
              <Chip
                label={varName}
                size="small"
                variant="outlined"
                sx={{
                  maxWidth: 160,
                  "& .MuiChip-label": {
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  },
                }}
              />
              <Iconify
                icon="solar:arrow-right-linear"
                width={14}
                sx={{ color: "text.disabled", flexShrink: 0 }}
              />
              <TextField
                select
                size="small"
                fullWidth
                value={varMapping[varName] || ""}
                onChange={(e) =>
                  setVarMapping((prev) => ({
                    ...prev,
                    [varName]: e.target.value,
                  }))
                }
                sx={fieldFontSx}
              >
                <MenuItem value="">
                  <em>None</em>
                </MenuItem>
                {columns.map((col) => (
                  <MenuItem key={col} value={col}>
                    {col}
                  </MenuItem>
                ))}
              </TextField>
            </Box>
          ))
        )}
      </Box>

      {/* Reference Output (required) + Eval Explanation (optional) */}
      <Box sx={sectionCardSx}>
        <Typography sx={sectionTitleSx}>Reference output</Typography>
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", fontSize: "12px", mt: -0.5 }}
        >
          The expected answer the evaluator should converge toward for each
          example.
        </Typography>
        {referenceHint && (
          <Typography
            variant="caption"
            sx={{ color: "status.warning.fg", fontSize: "12px", mt: -0.25 }}
          >
            Values in this column must match the eval&apos;s output type.{" "}
            {referenceHint}
          </Typography>
        )}
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Typography variant="caption" sx={labelColSx}>
            Output column *
          </Typography>
          <Iconify
            icon="solar:arrow-right-linear"
            width={14}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <TextField
            select
            size="small"
            fullWidth
            value={outputColumn}
            onChange={(e) => setOutputColumn(e.target.value)}
            sx={fieldFontSx}
            error={!outputColumn}
          >
            <MenuItem value="">
              <em>Pick a column</em>
            </MenuItem>
            {columns.map((col) => (
              <MenuItem key={col} value={col}>
                {col}
              </MenuItem>
            ))}
          </TextField>
        </Box>

        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Typography variant="caption" sx={labelColSx}>
            Explanation (optional)
          </Typography>
          <Iconify
            icon="solar:arrow-right-linear"
            width={14}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <TextField
            select
            size="small"
            fullWidth
            value={explanationColumn}
            onChange={(e) => setExplanationColumn(e.target.value)}
            sx={fieldFontSx}
          >
            <MenuItem value="">
              <em>None (optional)</em>
            </MenuItem>
            {columns.map((col) => (
              <MenuItem key={col} value={col}>
                {col}
              </MenuItem>
            ))}
          </TextField>
        </Box>
      </Box>

      {/* Retrieval knobs. */}
      <Box sx={sectionCardSx}>
        <Typography sx={sectionTitleSx}>Retrieval</Typography>
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", fontSize: "12px", mt: -0.5 }}
        >
          On each eval run, the rows most similar to the input are attached to
          the judge prompt as calibration examples.
        </Typography>
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Tooltip
            placement="top"
            arrow
            title="How many of the closest matching rows are shown to the judge. More examples sharpen calibration but slow down each run."
          >
            <Typography variant="caption" sx={labelColSx}>
              Examples shown ⓘ
            </Typography>
          </Tooltip>
          <Slider
            size="small"
            value={Number(maxExamples)}
            onChange={(_, v) => setMaxExamples(v)}
            min={1}
            max={10}
            step={1}
            marks
            valueLabelDisplay="auto"
            sx={{ flex: 1 }}
          />
          <Chip size="small" label={maxExamples} sx={{ minWidth: 36 }} />
        </Box>
      </Box>

      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 0.75,
          mt: 0.5,
        }}
      >
        {!outputColumn && (
          <Typography
            variant="caption"
            sx={{
              color: "status.danger.fg",
              display: "flex",
              alignItems: "center",
              gap: 0.5,
            }}
          >
            <Iconify icon="solar:danger-circle-linear" width={14} />
            Pick the column that holds the reference output.
          </Typography>
        )}
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Button
            variant={hasWork ? "contained" : "outlined"}
            onClick={handleCtaClick}
            disabled={ctaDisabled}
            startIcon={
              ctaPending ? (
                <CircularProgress size={14} />
              ) : hasWork ? (
                <Iconify icon="solar:diskette-linear" width={16} />
              ) : (
                <Iconify icon="solar:check-linear" width={16} />
              )
            }
            sx={{ flex: 1, minWidth: 0 }}
          >
            {ctaLabel}
          </Button>
          <Tooltip
            title={
              embedActive
                ? "Embeddings are still generating; this run may not return GT examples yet."
                : !embeddingsReady
                  ? "GT retrieval needs embeddings; run an Embed first for best results."
                  : "Try GT retrieval against a sample input"
            }
          >
            <span>
              <Button
                variant="text"
                onClick={onTest}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={16} />}
                disabled={!onTest || (isJevModel && persistedEnabled)}
                sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
              >
                Test eval
              </Button>
            </span>
          </Tooltip>
        </Box>
      </Box>
    </Box>
  );
};

// ═══════════════════════════════════════════════════════════════
// MAIN TAB
// ═══════════════════════════════════════════════════════════════
const EvalGroundTruthTab = ({
  templateId,
  onSwitchToDetails,
  isJevModel = false,
}) => {
  const { enqueueSnackbar } = useSnackbar();
  const { role } = useAuthContext();
  const canEdit = Boolean(
    RolePermission.EVALS[PERMISSIONS.EDIT_CREATE_DELETE_EVALS]?.[role],
  );
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const deleteInFlight = useRef(false);

  // Eval data - to get required_keys (variables)
  const { data: evalData } = useEvalDetail(templateId);
  const evalVariables = useMemo(() => {
    const config = evalData?.config || {};
    return config.requiredKeys || config.required_keys || [];
  }, [evalData]);
  const rulePrompt = useMemo(() => {
    const config = evalData?.config || {};
    return config.rulePrompt || config.rule_prompt || "";
  }, [evalData]);

  const { data: listData, isLoading: listLoading } =
    useGroundTruthList(templateId);
  const datasets = listData?.items || [];
  const activeDataset = datasets[0];

  // A confirmation applies only to the dataset and permissions it opened with.
  useEffect(() => {
    setDeleteTarget(null);
  }, [templateId, activeDataset?.id, canEdit]);

  // A newly active dataset always opens on its first preview page.
  const [previewPaging, setPreviewPaging] = useState({
    datasetId: null,
    page: 0,
  });
  const [previewPageSize, setPreviewPageSize] = useState(
    PREVIEW_DEFAULT_PAGE_SIZE,
  );
  const previewPage =
    previewPaging.datasetId === activeDataset?.id ? previewPaging.page : 0;
  const { data: previewData, isFetching: previewFetching } = useGroundTruthData(
    activeDataset?.id,
    {
      page: previewPage + 1,
      pageSize: previewPageSize,
    },
  );
  const { data: statusData } = useGroundTruthStatus(activeDataset?.id, {
    // Poll while the embed job is still in flight. The trigger view
    // sets `pending` synchronously and the Temporal activity flips
    // through `processing` before settling on a terminal status - both
    // need polling so the UI can react.
    enabled:
      activeDataset?.embedding_status === "pending" ||
      activeDataset?.embedding_status === "processing",
  });

  const deleteGt = useDeleteGroundTruth();
  const triggerEmbed = useTriggerEmbedding();

  const embeddingStatus =
    statusData?.embedding_status ||
    activeDataset?.embedding_status ||
    "pending";
  const embeddedCount = statusData?.embedded_row_count || 0;
  const totalRows = activeDataset?.row_count || 0;

  const handleDelete = useCallback(async () => {
    if (
      !deleteTarget ||
      deleteTarget.id !== activeDataset?.id ||
      !canEdit ||
      deleteGt.isPending ||
      deleteInFlight.current
    )
      return;
    // Block repeated clicks even before the mutation's pending state renders.
    deleteInFlight.current = true;
    try {
      await deleteGt.mutateAsync(deleteTarget.id);
      setDeleteTarget(null);
      enqueueSnackbar("Dataset deleted", { variant: "success" });
    } catch {
      enqueueSnackbar("Failed to delete", { variant: "error" });
    } finally {
      deleteInFlight.current = false;
    }
  }, [activeDataset?.id, canEdit, deleteGt, deleteTarget, enqueueSnackbar]);

  const handleTriggerEmbed = useCallback(async () => {
    if (!activeDataset) return;
    try {
      await triggerEmbed.mutateAsync(activeDataset.id);
      enqueueSnackbar("Embedding generation started", { variant: "info" });
    } catch (err) {
      enqueueSnackbar(
        getSafeActionErrorMessage(err, "Embedding didn't start. Try again."),
        { variant: "error" },
      );
      throw err;
    }
  }, [activeDataset, triggerEmbed, enqueueSnackbar]);

  // AG Grid theme
  const agTheme = useAgTheme();

  // AG Grid column definitions
  const agColDefs = useMemo(() => {
    const cols = activeDataset?.columns || previewData?.columns || [];
    return cols.map((col) => ({
      field: col,
      headerName: col,
      minWidth: 120,
      flex: 1,
      resizable: true,
      sortable: true,
      filter: true,
      editable: false,
      cellStyle: { fontSize: "12px" },
    }));
  }, [activeDataset?.columns, previewData?.columns]);

  const defaultColDef = useMemo(
    () => ({
      resizable: true,
      sortable: true,
      filter: true,
      suppressMovable: false,
      wrapText: false,
      autoHeight: false,
    }),
    [],
  );

  if (listLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100%",
        }}
      >
        <ContentSkeleton variant="panel" />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        gap: 2,
        overflow: "auto",
        pb: 2,
      }}
    >
      {isJevModel && (
        <Alert severity="warning">{JEV_GROUND_TRUTH_MESSAGE}</Alert>
      )}
      {/* Upload drawer */}
      <UploadDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        templateId={templateId}
        evalVariables={evalVariables}
        canEdit={canEdit}
      />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete dataset"
        body="This action cannot be undone."
        items={deleteTarget ? [deleteTarget.name] : []}
        confirmLabel="Delete"
        cancelLabel="Cancel"
        danger
        busy={deleteGt.isPending}
        onConfirm={handleDelete}
        onClose={() => {
          if (!deleteInFlight.current) setDeleteTarget(null);
        }}
      />

      {/* Empty state - clicks opens drawer */}
      {!datasets.length && (
        <EmptyState onUpload={() => setDrawerOpen(true)} canEdit={canEdit} />
      )}

      {/* Dataset header */}
      {activeDataset && (
        <>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              flexWrap: "wrap",
            }}
          >
            <Iconify
              icon="solar:database-linear"
              width={20}
              sx={{ color: "primary.main" }}
            />
            <Typography variant="body2" fontWeight={600}>
              {activeDataset.name}
            </Typography>
            <Chip label={`${totalRows} rows`} size="small" variant="outlined" />
            {isJevModel ? (
              <Chip
                label="Not supported with Jev"
                size="small"
                variant="outlined"
              />
            ) : (
              <StatusBadge status={embeddingStatus} />
            )}

            {(embeddingStatus === "processing" ||
              (embeddingStatus === "pending" && triggerEmbed.isPending)) && (
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  flex: 1,
                  maxWidth: 200,
                }}
              >
                <LinearProgress
                  variant={
                    embeddingStatus === "processing"
                      ? "determinate"
                      : "indeterminate"
                  }
                  value={
                    embeddingStatus === "processing" && totalRows > 0
                      ? (embeddedCount / totalRows) * 100
                      : undefined
                  }
                  sx={{ flex: 1, height: 4, borderRadius: 2 }}
                />
                <Typography variant="caption" color="text.secondary">
                  {embeddingStatus === "processing"
                    ? `${embeddedCount}/${totalRows}`
                    : "Queuing…"}
                </Typography>
              </Box>
            )}

            <Box sx={{ flex: 1 }} />

            <Tooltip title="Upload new dataset">
              <span>
                <IconButton
                  size="small"
                  aria-label="Upload new dataset"
                  onClick={() => setDrawerOpen(true)}
                  disabled={!canEdit}
                >
                  <Iconify icon="solar:upload-minimalistic-linear" width={16} />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Delete dataset">
              <span>
                <IconButton
                  size="small"
                  color="error"
                  aria-label="Delete dataset"
                  onClick={() => setDeleteTarget(activeDataset)}
                  disabled={deleteGt.isPending || !canEdit}
                >
                  <Iconify icon="solar:trash-bin-trash-linear" width={16} />
                </IconButton>
              </span>
            </Tooltip>
          </Box>

          {/* Two-column: settings + data */}
          <Box sx={{ display: "flex", gap: 2, flex: 1, minHeight: 0 }}>
            {/* Left: settings */}
            <Box
              sx={{
                width: 420,
                flexShrink: 0,
                display: "flex",
                flexDirection: "column",
                gap: 2,
                overflow: "auto",
                pr: 1,
              }}
            >
              <GroundTruthSetupForm
                gt={activeDataset}
                template={evalData}
                isJevModel={isJevModel}
                evalVariables={evalVariables}
                rulePrompt={rulePrompt}
                onEmbed={handleTriggerEmbed}
                onTest={onSwitchToDetails}
                embedPending={triggerEmbed.isPending}
                embeddingStatus={embeddingStatus}
                embeddingsStale={Boolean(
                  activeDataset?.embeddings_stale ||
                    activeDataset?.embeddingsStale,
                )}
                canEdit={canEdit}
              />
            </Box>
            {/* Right: data preview - AG Grid spreadsheet */}
            <Box
              sx={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                minWidth: 0,
                minHeight: 0,
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  mb: 1,
                }}
              >
                <Typography
                  variant="body2"
                  fontWeight={600}
                  sx={{ fontSize: "12px" }}
                >
                  Data Preview
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {totalRows} rows
                </Typography>
              </Box>
              <Box
                sx={{
                  flex: 1,
                  minHeight: 200,
                }}
              >
                <AgGridReact
                  theme={agTheme}
                  columnDefs={agColDefs}
                  rowData={previewData?.rows || []}
                  defaultColDef={defaultColDef}
                  headerHeight={34}
                  rowHeight={ROW_HEIGHT.default}
                  animateRows={false}
                  suppressCellFocus
                  enableCellTextSelection
                  ensureDomOrder
                  overlayNoRowsTemplate="<span style='font-size:13px;opacity:0.5'>No data</span>"
                  overlayLoadingTemplate="<span style='font-size:13px;opacity:0.5'>Loading...</span>"
                  loading={!previewData}
                />
              </Box>
              <DataTablePagination
                page={previewPage}
                pageSize={previewPageSize}
                total={previewData?.total_rows ?? totalRows}
                pageSizeOptions={PREVIEW_PAGE_SIZE_OPTIONS}
                loading={Boolean(previewFetching)}
                onPageChange={(page) =>
                  setPreviewPaging({ datasetId: activeDataset.id, page })
                }
                onPageSizeChange={(size) => {
                  setPreviewPageSize(size);
                  setPreviewPaging({ datasetId: activeDataset.id, page: 0 });
                }}
              />
            </Box>
          </Box>
        </>
      )}
    </Box>
  );
};

EvalGroundTruthTab.propTypes = {
  templateId: PropTypes.string.isRequired,
  isJevModel: PropTypes.bool,
};

export default EvalGroundTruthTab;
