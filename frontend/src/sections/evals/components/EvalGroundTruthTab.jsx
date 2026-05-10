/* eslint-disable react/prop-types */
import {
  Box,
  Button,
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
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useMemo, useState } from "react";
import { useDropzone } from "react-dropzone";
import { useSnackbar } from "notistack";
import { AgGridReact } from "ag-grid-react";
import { useAgTheme } from "src/hooks/use-ag-theme";
import Iconify from "src/components/iconify";
import { canonicalEntries } from "src/utils/utils";

import {
  useDevelopDatasetList,
  useGetDatasetColumns,
  useGetDatasetDetail,
} from "src/api/develop/develop-detail";
import { useEvalDetail } from "../hooks/useEvalDetail";
import {
  useDeleteGroundTruth,
  useGroundTruthConfig,
  useGroundTruthData,
  useGroundTruthList,
  useGroundTruthStatus,
  useSearchGroundTruth,
  useTriggerEmbedding,
  useUpdateGroundTruthConfig,
  useUpdateRoleMapping,
  useUpdateVariableMapping,
  useUploadGroundTruth,
} from "../hooks/useGroundTruth";

// ═══════════════════════════════════════════════════════════════
// Status Badge
// ═══════════════════════════════════════════════════════════════
const StatusBadge = ({ status }) => {
  const map = {
    pending: { label: "Pending", color: "default", icon: "mdi:clock-outline" },
    processing: {
      label: "Embedding...",
      color: "warning",
      icon: "mdi:loading",
    },
    completed: {
      label: "Ready",
      color: "success",
      icon: "mdi:check-circle-outline",
    },
    failed: {
      label: "Failed",
      color: "error",
      icon: "mdi:alert-circle-outline",
    },
  };
  const info = map[status] || map.pending;
  return (
    <Chip
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
      label={info.label}
      size="small"
      color={info.color}
      variant="outlined"
      sx={{ fontSize: "11px", height: 22 }}
    />
  );
};

// ═══════════════════════════════════════════════════════════════
// Upload Drawer — right sidebar like KB
// ═══════════════════════════════════════════════════════════════
const ACCEPTED_TYPES = {
  "text/csv": [".csv"],
  "application/json": [".json"],
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [
    ".xlsx",
  ],
  "application/vnd.ms-excel": [".xls"],
};

const UploadDrawer = ({ open, onClose, templateId, evalVariables }) => {
  const { enqueueSnackbar } = useSnackbar();
  const upload = useUploadGroundTruth(templateId);

  // Steps: 0 = choose source, 1 = configure (file), 2 = pick dataset, 3 = configure (dataset)
  const [step, setStep] = useState(0);
  const [file, setFile] = useState(null);
  const [name, setName] = useState("");
  const [variableMapping, setVariableMapping] = useState({});
  const [parsedColumns, setParsedColumns] = useState([]);

  // Dataset selection state
  const [datasetSearch, setDatasetSearch] = useState("");
  const [selectedDataset, setSelectedDataset] = useState(null);
  const [loadingDatasetData, setLoadingDatasetData] = useState(false);

  // Fetch datasets list
  const { data: datasets = [], isLoading: datasetsLoading } =
    useDevelopDatasetList(datasetSearch, [], {}, {});

  // Fetch selected dataset's columns
  const _selectedDatasetId = selectedDataset?.dataset_id || selectedDataset?.id;
  const { data: datasetColumns } = useGetDatasetColumns(_selectedDatasetId, {
    enabled: !!_selectedDatasetId,
  });

  const reset = useCallback(() => {
    setStep(0);
    setFile(null);
    setName("");
    setVariableMapping({});
    setParsedColumns([]);
    setDatasetSearch("");
    setSelectedDataset(null);
    setLoadingDatasetData(false);
  }, []);

  const handleClose = useCallback(() => {
    reset();
    onClose();
  }, [reset, onClose]);

  // ── File upload flow ──
  const onDrop = useCallback((accepted) => {
    if (!accepted.length) return;
    const f = accepted[0];
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
        try {
          const parsed = JSON.parse(e.target.result);
          const arr = Array.isArray(parsed) ? parsed : parsed.data || [parsed];
          if (arr.length > 0) setParsedColumns(Object.keys(arr[0]));
        } catch {
          /* ignore */
        }
      };
      reader.readAsText(f.slice(0, 65536));
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: ACCEPTED_TYPES,
    multiple: false,
    maxSize: 50 * 1024 * 1024,
  });

  const handleFileUpload = useCallback(async () => {
    if (!file || !name) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    if (Object.keys(variableMapping).length > 0) {
      fd.append("variable_mapping", JSON.stringify(variableMapping));
    }
    try {
      await upload.mutateAsync(fd);
      enqueueSnackbar("Dataset uploaded successfully", { variant: "success" });
      handleClose();
    } catch (err) {
      enqueueSnackbar(err?.response?.data?.message || "Upload failed", {
        variant: "error",
      });
    }
  }, [file, name, variableMapping, upload, enqueueSnackbar, handleClose]);

  // ── Dataset selection flow ──
  const handleDatasetSelect = useCallback((ds) => {
    setSelectedDataset(ds);
    setName(ds.name);
    setStep(3);
  }, []);

  // Derive column names from dataset columns response
  const datasetColumnNames = useMemo(() => {
    if (!datasetColumns) return [];
    return datasetColumns.map(
      (col) => col.name || col.label || col.id || String(col),
    );
  }, [datasetColumns]);

  const handleDatasetUpload = useCallback(async () => {
    if (!selectedDataset || !name) return;
    setLoadingDatasetData(true);
    try {
      // Fetch dataset rows
      const datasetId = selectedDataset.dataset_id || selectedDataset.id;
      const { data: res } = await (
        await import("src/utils/axios")
      ).default.get(`/model-hub/develops/${datasetId}/get-dataset-table/`, {
        params: { current_page_index: 0, page_size: 10000 },
      });
      const tableData = res?.result;
      const tableRows = tableData?.table || [];

      // Build column ID → name map from already-fetched datasetColumns
      const colMap = {};
      (datasetColumns || []).forEach((col) => {
        const colId = String(col.id || col.column_id);
        colMap[colId] = col.name || col.label || colId;
      });

      const colNames = Object.values(colMap);
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
        setLoadingDatasetData(false);
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
        err?.response?.data?.message || "Failed to import dataset",
        { variant: "error" },
      );
    } finally {
      setLoadingDatasetData(false);
    }
  }, [
    selectedDataset,
    name,
    variableMapping,
    upload,
    enqueueSnackbar,
    handleClose,
  ]);

  // Columns for the mapping step (from file or from dataset)
  const activeColumns = step === 1 ? parsedColumns : datasetColumnNames;
  const isConfigStep = step === 1 || step === 3;
  const isSubmitting = upload.isPending || loadingDatasetData;

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      PaperProps={{
        sx: {
          width: 520,
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
          <IconButton size="small" onClick={handleClose}>
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Box>

        {/* Steps indicator */}
        <Stepper
          activeStep={step === 2 ? 0 : isConfigStep ? 1 : 0}
          alternativeLabel
          sx={{ mb: 3, "& .MuiStepLabel-label": { fontSize: "11px" } }}
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
                borderColor: isDragActive ? "primary.main" : "divider",
                borderRadius: "10px",
                p: 4,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 1.5,
                cursor: "pointer",
                transition: "all 0.2s",
                bgcolor: isDragActive
                  ? (t) =>
                      t.palette.mode === "dark"
                        ? "rgba(124,77,255,0.08)"
                        : "rgba(124,77,255,0.04)"
                  : "transparent",
                "&:hover": {
                  borderColor: "primary.main",
                  bgcolor: (t) =>
                    t.palette.mode === "dark"
                      ? "rgba(255,255,255,0.03)"
                      : "rgba(0,0,0,0.02)",
                },
              }}
            >
              <input {...getInputProps()} />
              <Box
                sx={{
                  width: 48,
                  height: 48,
                  borderRadius: "10px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  bgcolor: (t) =>
                    t.palette.mode === "dark"
                      ? "rgba(124,77,255,0.12)"
                      : "rgba(124,77,255,0.08)",
                }}
              >
                <Iconify
                  icon="mdi:cloud-upload-outline"
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
                CSV, Excel (.xls, .xlsx), or JSON — up to 50 MB
              </Typography>
              <Button
                variant="outlined"
                size="small"
                sx={{
                  mt: 0.5,
                  px: 3,
                  borderRadius: "8px",
                  borderColor: "divider",
                  color: "text.primary",
                }}
              >
                Browse files
              </Button>
            </Box>

            <Divider sx={{ my: 0.5 }}>
              <Typography variant="caption" color="text.disabled">
                or
              </Typography>
            </Divider>

            {/* From existing dataset */}
            <Box
              onClick={() => setStep(2)}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "10px",
                p: 2.5,
                display: "flex",
                alignItems: "center",
                gap: 2,
                cursor: "pointer",
                transition: "all 0.2s",
                "&:hover": {
                  borderColor: "primary.main",
                  bgcolor: (t) =>
                    t.palette.mode === "dark"
                      ? "rgba(255,255,255,0.03)"
                      : "rgba(0,0,0,0.02)",
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
                  bgcolor: (t) =>
                    t.palette.mode === "dark"
                      ? "rgba(255,255,255,0.06)"
                      : "background.neutral",
                }}
              >
                <Iconify
                  icon="mdi:database-outline"
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
                icon="mdi:chevron-right"
                width={20}
                sx={{ color: "text.disabled" }}
              />
            </Box>
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
                    icon="mdi:magnify"
                    width={18}
                    sx={{ mr: 0.5, color: "text.disabled" }}
                  />
                ),
              }}
              sx={{ "& .MuiInputBase-input": { fontSize: "13px" } }}
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
                <Box
                  key={ds.dataset_id || ds.id}
                  onClick={() => handleDatasetSelect(ds)}
                  sx={{
                    p: 1.5,
                    borderRadius: "8px",
                    border: "1px solid",
                    borderColor: "divider",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 1.5,
                    transition: "all 0.15s",
                    "&:hover": {
                      borderColor: "primary.main",
                      bgcolor: (t) =>
                        t.palette.mode === "dark"
                          ? "rgba(255,255,255,0.03)"
                          : "rgba(0,0,0,0.015)",
                    },
                  }}
                >
                  <Iconify
                    icon="mdi:table"
                    width={18}
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
                    icon="mdi:chevron-right"
                    width={18}
                    sx={{ color: "text.disabled", flexShrink: 0 }}
                  />
                </Box>
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
                  borderColor: "divider",
                }}
              >
                <Iconify
                  icon={
                    file.name.endsWith(".csv")
                      ? "mdi:file-delimited-outline"
                      : file.name.endsWith(".json")
                        ? "mdi:code-json"
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
                  onClick={() => {
                    setFile(null);
                    setStep(0);
                    setParsedColumns([]);
                  }}
                >
                  <Iconify icon="mdi:close" width={16} />
                </IconButton>
              </Box>
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
                  icon="mdi:table"
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
                  onClick={() => {
                    setSelectedDataset(null);
                    setStep(2);
                  }}
                >
                  <Iconify icon="mdi:close" width={16} />
                </IconButton>
              </Box>
            )}

            {/* Dataset name */}
            <TextField
              size="small"
              label="Ground truth name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              fullWidth
              sx={{ "& .MuiInputBase-input": { fontSize: "13px" } }}
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
                      <Chip
                        label={`{{${varName}}}`}
                        size="small"
                        variant="outlined"
                        sx={{
                          fontSize: "11px",
                          height: 24,
                          minWidth: 100,
                          fontFamily: "monospace",
                        }}
                      />
                      <Iconify
                        icon="mdi:arrow-right"
                        width={14}
                        sx={{ color: "text.disabled", flexShrink: 0 }}
                      />
                      <TextField
                        select
                        size="small"
                        fullWidth
                        value={variableMapping[varName] || ""}
                        onChange={(e) =>
                          setVariableMapping((prev) => ({
                            ...prev,
                            [varName]: e.target.value,
                          }))
                        }
                        sx={{ "& .MuiInputBase-input": { fontSize: "12px" } }}
                      >
                        <MenuItem value="">
                          <em>— skip —</em>
                        </MenuItem>
                        {activeColumns.map((col) => (
                          <MenuItem
                            key={col}
                            value={col}
                            sx={{ fontSize: "12px" }}
                          >
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
                      sx={{ fontSize: "10px", height: 20 }}
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
              onClick={() => setStep(step === 3 ? 2 : 0)}
              sx={{ flex: 1 }}
            >
              Back
            </Button>
            <Button
              variant="contained"
              size="small"
              onClick={step === 1 ? handleFileUpload : handleDatasetUpload}
              disabled={
                (step === 1 && (!file || !name)) ||
                (step === 3 && (!selectedDataset || !name)) ||
                isSubmitting
              }
              sx={{ flex: 1 }}
            >
              {isSubmitting ? (
                <CircularProgress size={16} sx={{ color: "inherit" }} />
              ) : step === 3 ? (
                "Import"
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
const EmptyState = ({ onUpload }) => (
  <Box
    onClick={onUpload}
    sx={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      py: 8,
      gap: 2,
      height: "100%",
      cursor: "pointer",
      borderRadius: "12px",
      border: "1px dashed",
      borderColor: "divider",
      transition: "all 0.2s",
      "&:hover": {
        borderColor: "primary.main",
        bgcolor: (t) =>
          t.palette.mode === "dark"
            ? "rgba(255,255,255,0.02)"
            : "rgba(0,0,0,0.01)",
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
        bgcolor: (t) =>
          t.palette.mode === "dark"
            ? "rgba(255,255,255,0.06)"
            : "background.neutral",
      }}
    >
      <Iconify
        icon="mdi:database-plus-outline"
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
    <Typography variant="caption" color="text.disabled">
      Click anywhere to upload
    </Typography>
  </Box>
);

// ═══════════════════════════════════════════════════════════════
// Variable Mapping Section — maps eval {{variables}} to GT columns
// ═══════════════════════════════════════════════════════════════
const VariableMappingSection = ({ gt, evalVariables, onUpdate }) => {
  const [mapping, setMapping] = useState(
    gt.variable_mapping || gt.variableMapping || {},
  );
  const updateMapping = useUpdateVariableMapping();
  const { enqueueSnackbar } = useSnackbar();

  const handleSave = useCallback(async () => {
    const filtered = Object.fromEntries(
      Object.entries(mapping).filter(([, v]) => v),
    );
    try {
      await updateMapping.mutateAsync({
        gtId: gt.id,
        variableMapping: filtered,
      });
      enqueueSnackbar("Variable mapping saved", { variant: "success" });
      onUpdate?.();
    } catch (err) {
      enqueueSnackbar(
        err?.response?.data?.message || "Failed to save mapping",
        { variant: "error" },
      );
    }
  }, [mapping, gt.id, updateMapping, enqueueSnackbar, onUpdate]);

  if (!evalVariables.length) return null;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Typography variant="body2" fontWeight={600} sx={{ fontSize: "12px" }}>
        Variable Mapping
      </Typography>
      <Typography variant="caption" color="text.secondary">
        Map eval template variables to ground truth columns. These are
        substituted into the eval prompt at runtime.
      </Typography>
      {evalVariables.map((varName) => (
        <Box
          key={varName}
          sx={{ display: "flex", alignItems: "center", gap: 1 }}
        >
          <Chip
            label={`{{${varName}}}`}
            size="small"
            variant="outlined"
            sx={{
              fontSize: "11px",
              height: 24,
              minWidth: 100,
              fontFamily: "monospace",
            }}
          />
          <Iconify
            icon="mdi:arrow-right"
            width={14}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <TextField
            select
            size="small"
            fullWidth
            value={mapping[varName] || ""}
            onChange={(e) =>
              setMapping((prev) => ({ ...prev, [varName]: e.target.value }))
            }
            sx={{ "& .MuiInputBase-input": { fontSize: "12px" } }}
          >
            <MenuItem value="">
              <em>None</em>
            </MenuItem>
            {(gt.columns || []).map((col) => (
              <MenuItem key={col} value={col} sx={{ fontSize: "12px" }}>
                {col}
              </MenuItem>
            ))}
          </TextField>
        </Box>
      ))}
      <Button
        size="small"
        variant="outlined"
        onClick={handleSave}
        disabled={updateMapping.isPending}
        sx={{ alignSelf: "flex-start", mt: 0.5 }}
      >
        {updateMapping.isPending ? "Saving..." : "Save Mapping"}
      </Button>
    </Box>
  );
};

// ═══════════════════════════════════════════════════════════════
// Role Mapping Section — maps semantic roles for few-shot formatting
// ═══════════════════════════════════════════════════════════════
const RoleMappingSection = ({ gt, onUpdate }) => {
  const [roleMapping, setRoleMapping] = useState(
    gt.roleMapping || gt.role_mapping || {},
  );
  const updateRole = useUpdateRoleMapping();
  const { enqueueSnackbar } = useSnackbar();

  const roles = [
    { key: "input", label: "Input", desc: "The input/question column" },
    {
      key: "expected_output",
      label: "Expected Output",
      desc: "The expected/reference answer",
    },
    { key: "score", label: "Score", desc: "Human-assigned score (0-1)" },
    { key: "reasoning", label: "Reasoning", desc: "Explanation for the score" },
  ];

  const handleSave = useCallback(async () => {
    const filtered = Object.fromEntries(
      Object.entries(roleMapping).filter(([, v]) => v),
    );
    try {
      await updateRole.mutateAsync({ gtId: gt.id, roleMapping: filtered });
      enqueueSnackbar("Role mapping saved", { variant: "success" });
      onUpdate?.();
    } catch (err) {
      enqueueSnackbar(
        err?.response?.data?.message || "Failed to save mapping",
        { variant: "error" },
      );
    }
  }, [roleMapping, gt.id, updateRole, enqueueSnackbar, onUpdate]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Typography variant="body2" fontWeight={600} sx={{ fontSize: "12px" }}>
        Role Mapping{" "}
        <Typography component="span" variant="caption" color="text.secondary">
          (for few-shot formatting)
        </Typography>
      </Typography>
      {roles.map(({ key, label, desc }) => (
        <Box key={key} sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Tooltip title={desc} placement="left">
            <Typography
              variant="caption"
              sx={{ width: 110, flexShrink: 0, color: "text.secondary" }}
            >
              {label}
            </Typography>
          </Tooltip>
          <Iconify
            icon="mdi:arrow-right"
            width={14}
            sx={{ color: "text.disabled", flexShrink: 0 }}
          />
          <TextField
            select
            size="small"
            fullWidth
            value={roleMapping[key] || ""}
            onChange={(e) =>
              setRoleMapping((prev) => ({ ...prev, [key]: e.target.value }))
            }
            sx={{ "& .MuiInputBase-input": { fontSize: "12px" } }}
          >
            <MenuItem value="">
              <em>None</em>
            </MenuItem>
            {(gt.columns || []).map((col) => (
              <MenuItem key={col} value={col} sx={{ fontSize: "12px" }}>
                {col}
              </MenuItem>
            ))}
          </TextField>
        </Box>
      ))}
      <Button
        size="small"
        variant="outlined"
        onClick={handleSave}
        disabled={updateRole.isPending}
        sx={{ alignSelf: "flex-start", mt: 0.5 }}
      >
        {updateRole.isPending ? "Saving..." : "Save Mapping"}
      </Button>
    </Box>
  );
};

// ═══════════════════════════════════════════════════════════════
// Injection Config
// ═══════════════════════════════════════════════════════════════
const ConfigPanel = ({ templateId, gtId }) => {
  const { data: config } = useGroundTruthConfig(templateId);
  const updateConfig = useUpdateGroundTruthConfig(templateId);
  const { enqueueSnackbar } = useSnackbar();
  const [maxExamples, setMaxExamples] = useState(
    config?.maxExamples ?? config?.max_examples ?? 3,
  );
  const [threshold, setThreshold] = useState(
    config?.similarityThreshold ?? config?.similarity_threshold ?? 0.7,
  );
  const enabled = config?.enabled ?? false;

  const handleToggle = useCallback(async () => {
    try {
      await updateConfig.mutateAsync({
        enabled: !enabled,
        ground_truth_id: gtId,
        mode: "auto",
        max_examples: maxExamples,
        similarity_threshold: threshold,
      });
      enqueueSnackbar(
        enabled ? "Ground truth disabled" : "Ground truth enabled",
        { variant: "success" },
      );
    } catch {
      enqueueSnackbar("Failed to update config", { variant: "error" });
    }
  }, [enabled, gtId, maxExamples, threshold, updateConfig, enqueueSnackbar]);

  const handleSave = useCallback(async () => {
    try {
      await updateConfig.mutateAsync({
        enabled: true,
        ground_truth_id: gtId,
        mode: "auto",
        max_examples: maxExamples,
        similarity_threshold: threshold,
      });
      enqueueSnackbar("Config saved", { variant: "success" });
    } catch {
      enqueueSnackbar("Failed to save config", { variant: "error" });
    }
  }, [gtId, maxExamples, threshold, updateConfig, enqueueSnackbar]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography variant="body2" fontWeight={600} sx={{ fontSize: "12px" }}>
          Injection Settings
        </Typography>
        <Button
          size="small"
          variant={enabled ? "contained" : "outlined"}
          color={enabled ? "success" : "inherit"}
          onClick={handleToggle}
          sx={{ fontSize: "11px", height: 26 }}
        >
          {enabled ? "Enabled" : "Disabled"}
        </Button>
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ width: 110, flexShrink: 0 }}
        >
          Few-shot examples
        </Typography>
        <Slider
          size="small"
          value={maxExamples}
          onChange={(_, v) => setMaxExamples(v)}
          min={1}
          max={10}
          step={1}
          valueLabelDisplay="auto"
          sx={{ flex: 1 }}
        />
        <Typography variant="caption" sx={{ width: 20, textAlign: "right" }}>
          {maxExamples}
        </Typography>
      </Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ width: 110, flexShrink: 0 }}
        >
          Min similarity
        </Typography>
        <Slider
          size="small"
          value={threshold}
          onChange={(_, v) => setThreshold(v)}
          min={0}
          max={1}
          step={0.05}
          valueLabelDisplay="auto"
          sx={{ flex: 1 }}
        />
        <Typography variant="caption" sx={{ width: 30, textAlign: "right" }}>
          {threshold}
        </Typography>
      </Box>
      <Button
        size="small"
        variant="outlined"
        onClick={handleSave}
        disabled={updateConfig.isPending}
        sx={{ alignSelf: "flex-start" }}
      >
        Save Config
      </Button>
    </Box>
  );
};

// ═══════════════════════════════════════════════════════════════
// Test Retrieval
// ═══════════════════════════════════════════════════════════════
const TestRetrieval = ({ gtId }) => {
  const [query, setQuery] = useState("");
  const search = useSearchGroundTruth();

  const handleSearch = useCallback(() => {
    if (!query.trim()) return;
    search.mutate({ gtId, query: query.trim(), maxResults: 3 });
  }, [gtId, query, search]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Typography variant="body2" fontWeight={600} sx={{ fontSize: "12px" }}>
        Test Retrieval
      </Typography>
      <Box sx={{ display: "flex", gap: 1 }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Enter a query to test similarity search..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          sx={{ "& .MuiInputBase-input": { fontSize: "12px" } }}
        />
        <Button
          size="small"
          variant="outlined"
          onClick={handleSearch}
          disabled={search.isPending || !query.trim()}
          sx={{ flexShrink: 0, minWidth: 70 }}
        >
          {search.isPending ? <CircularProgress size={14} /> : "Search"}
        </Button>
      </Box>
      {search.data?.results?.length > 0 && (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1,
            maxHeight: 200,
            overflow: "auto",
          }}
        >
          {search.data.results.map((r, i) => (
            <Box
              key={i}
              sx={{
                p: 1,
                borderRadius: "6px",
                border: "1px solid",
                borderColor: "divider",
                fontSize: "11px",
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  mb: 0.5,
                }}
              >
                <Typography variant="caption" fontWeight={600}>
                  Match {i + 1}
                </Typography>
                <Chip
                  label={`${(r.similarity * 100).toFixed(0)}%`}
                  size="small"
                  color={
                    r.similarity > 0.8
                      ? "success"
                      : r.similarity > 0.6
                        ? "warning"
                        : "default"
                  }
                  sx={{ fontSize: "10px", height: 18 }}
                />
              </Box>
              {canonicalEntries(r.row_data || r.rowData || {})
                .slice(0, 4)
                .map(([k, v]) => (
                  <Typography
                    key={k}
                    variant="caption"
                    color="text.secondary"
                    component="div"
                    noWrap
                  >
                    <strong>{k}:</strong> {String(v).slice(0, 100)}
                  </Typography>
                ))}
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
};

// ═══════════════════════════════════════════════════════════════
// MAIN TAB
// ═══════════════════════════════════════════════════════════════
const EvalGroundTruthTab = ({ templateId }) => {
  const { enqueueSnackbar } = useSnackbar();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Eval data — to get required_keys (variables)
  const { data: evalData } = useEvalDetail(templateId);
  const evalVariables = useMemo(() => {
    const config = evalData?.config || {};
    return config.requiredKeys || config.required_keys || [];
  }, [evalData]);

  const { data: listData, isLoading: listLoading } =
    useGroundTruthList(templateId);
  const datasets = listData?.items || [];
  const activeDataset = datasets[0];

  const { data: previewData } = useGroundTruthData(activeDataset?.id, {
    page: 1,
    pageSize: 500,
  });
  const { data: statusData } = useGroundTruthStatus(activeDataset?.id, {
    enabled:
      (activeDataset?.embedding_status || activeDataset?.embeddingStatus) ===
      "processing",
  });

  const deleteGt = useDeleteGroundTruth();
  const triggerEmbed = useTriggerEmbedding();

  const embeddingStatus =
    statusData?.embedding_status ||
    activeDataset?.embedding_status ||
    activeDataset?.embeddingStatus ||
    "pending";
  const embeddedCount =
    statusData?.embedded_row_count || statusData?.embeddedRowCount || 0;
  const totalRows = activeDataset?.row_count || activeDataset?.rowCount || 0;

  const handleDelete = useCallback(async () => {
    if (!activeDataset) return;
    try {
      await deleteGt.mutateAsync(activeDataset.id);
      enqueueSnackbar("Dataset deleted", { variant: "success" });
    } catch {
      enqueueSnackbar("Failed to delete", { variant: "error" });
    }
  }, [activeDataset, deleteGt, enqueueSnackbar]);

  const handleTriggerEmbed = useCallback(async () => {
    if (!activeDataset) return;
    try {
      await triggerEmbed.mutateAsync(activeDataset.id);
      enqueueSnackbar("Embedding generation started", { variant: "info" });
    } catch (err) {
      enqueueSnackbar(
        err?.response?.data?.message || "Failed to trigger embedding",
        { variant: "error" },
      );
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
        <CircularProgress size={24} />
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
      {/* Upload drawer */}
      <UploadDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        templateId={templateId}
        evalVariables={evalVariables}
      />

      {/* Empty state — clicks opens drawer */}
      {!datasets.length && <EmptyState onUpload={() => setDrawerOpen(true)} />}

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
              icon="mdi:database-outline"
              width={18}
              sx={{ color: "primary.main" }}
            />
            <Typography variant="body2" fontWeight={600}>
              {activeDataset.name}
            </Typography>
            <Chip
              label={`${totalRows} rows`}
              size="small"
              variant="outlined"
              sx={{ fontSize: "11px", height: 20 }}
            />
            <StatusBadge status={embeddingStatus} />

            {embeddingStatus === "processing" && (
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
                  variant="determinate"
                  value={totalRows > 0 ? (embeddedCount / totalRows) * 100 : 0}
                  sx={{ flex: 1, height: 4, borderRadius: 2 }}
                />
                <Typography variant="caption" color="text.secondary">
                  {embeddedCount}/{totalRows}
                </Typography>
              </Box>
            )}

            <Box sx={{ flex: 1 }} />

            {(embeddingStatus === "pending" ||
              embeddingStatus === "failed") && (
              <Tooltip title="Generate embeddings for similarity search">
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<Iconify icon="mdi:brain" width={14} />}
                  onClick={handleTriggerEmbed}
                  disabled={triggerEmbed.isPending}
                  sx={{ fontSize: "11px", height: 26 }}
                >
                  Embed
                </Button>
              </Tooltip>
            )}

            <Tooltip title="Upload new dataset">
              <IconButton size="small" onClick={() => setDrawerOpen(true)}>
                <Iconify icon="mdi:upload" width={16} />
              </IconButton>
            </Tooltip>
            <Tooltip title="Delete dataset">
              <IconButton
                size="small"
                color="error"
                onClick={handleDelete}
                disabled={deleteGt.isPending}
              >
                <Iconify icon="mdi:delete-outline" width={16} />
              </IconButton>
            </Tooltip>
          </Box>

          {/* Two-column: settings + data */}
          <Box sx={{ display: "flex", gap: 2, flex: 1, minHeight: 0 }}>
            {/* Left: settings */}
            <Box
              sx={{
                width: 320,
                flexShrink: 0,
                display: "flex",
                flexDirection: "column",
                gap: 2.5,
                overflow: "auto",
                pr: 1,
              }}
            >
              <VariableMappingSection
                gt={activeDataset}
                evalVariables={evalVariables}
                onUpdate={() => {}}
              />
              {evalVariables.length > 0 && <Divider />}
              <RoleMappingSection gt={activeDataset} onUpdate={() => {}} />
              <Divider />
              <ConfigPanel templateId={templateId} gtId={activeDataset.id} />
              {embeddingStatus === "completed" && (
                <>
                  <Divider />
                  <TestRetrieval gtId={activeDataset.id} />
                </>
              )}
            </Box>
            {/* Right: data preview — AG Grid spreadsheet */}
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
                  borderRadius: "8px",
                  overflow: "hidden",
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <AgGridReact
                  theme={agTheme}
                  columnDefs={agColDefs}
                  rowData={previewData?.rows || []}
                  defaultColDef={defaultColDef}
                  headerHeight={34}
                  rowHeight={32}
                  animateRows={false}
                  suppressCellFocus
                  enableCellTextSelection
                  ensureDomOrder
                  pagination
                  paginationPageSize={50}
                  paginationPageSizeSelector={[25, 50, 100]}
                  overlayNoRowsTemplate="<span style='font-size:13px;opacity:0.5'>No data</span>"
                  overlayLoadingTemplate="<span style='font-size:13px;opacity:0.5'>Loading...</span>"
                  loading={!previewData}
                />
              </Box>
            </Box>
          </Box>
        </>
      )}
    </Box>
  );
};

EvalGroundTruthTab.propTypes = {
  templateId: PropTypes.string.isRequired,
};

export default EvalGroundTruthTab;
