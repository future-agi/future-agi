import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  Drawer,
  FormControlLabel,
  IconButton,
  InputAdornment,
  LinearProgress,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useAnnotationQueueExportFields,
  useExportToDataset,
} from "src/api/annotation-queues/annotation-queues";
import {
  useDevelopDatasetList,
  useGetDatasetColumns,
} from "src/api/develop/develop-detail";

const STATUS_OPTIONS = [
  { value: "completed", label: "Completed only" },
  { value: "", label: "All items" },
  { value: "pending", label: "Pending only" },
  { value: "in_progress", label: "In Progress only" },
];

const CUSTOM_FIELD_VALUE = "__custom_attribute__";

const emptyMapping = () => ({
  field: "attr:",
  column: "",
  enabled: true,
});

const customPathFromField = (field) =>
  field?.startsWith("attr:") ? field.replace(/^attr:/, "") : "";

const attributePathFromField = (field) => {
  const path = customPathFromField(field);
  const [, scopedPath] = path.split(/:(.+)/);
  return scopedPath || path;
};

const displayAttributePath = (path) =>
  (path || "").replace(/^span_attributes\./, "");

const isKnownField = (field, fieldsById) => Boolean(fieldsById.get(field));

const isAttributeOption = (option) => option?.id?.startsWith("attr:");

const fieldOptionLabel = (option) => {
  if (!option) return "";
  if (isAttributeOption(option)) {
    return displayAttributePath(
      option.path || attributePathFromField(option.id) || option.label || "",
    );
  }
  return option.label || "";
};

const fieldOptionDescription = (option) => {
  if (!option?.path || isAttributeOption(option)) return "";
  return option.path === option.label ? "" : option.path;
};

const mappingFromField = (field) => ({
  field: field.id,
  column: isAttributeOption(field)
    ? displayAttributePath(
        field.column || field.path || attributePathFromField(field.id),
      )
    : field.column || field.id,
  enabled: true,
});

const customFieldOption = {
  id: CUSTOM_FIELD_VALUE,
  label: "Custom attribute path",
  group: "Custom",
};

const datasetOptionId = (dataset) => dataset?.datasetId || dataset?.id || "";

const datasetOptionLabel = (dataset) =>
  dataset?.name || dataset?.dataset_name || datasetOptionId(dataset);

const datasetColumnName = (column) =>
  typeof column === "string"
    ? column
    : column?.name || column?.column_name || "";

const optionTitle = (...parts) => parts.filter(Boolean).join("\n");

export default function ExportToDatasetDialog({ open, onClose, queueId }) {
  const [mode, setMode] = useState("new");
  const [datasetName, setDatasetName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [selectedDataset, setSelectedDataset] = useState(null);
  const [datasetSearch, setDatasetSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("completed");
  const [mapping, setMapping] = useState([]);
  const [exportSubmitting, setExportSubmitting] = useState(false);
  const exportTimerRef = useRef(null);

  const { mutate: exportToDataset, isPending } = useExportToDataset();
  const { data: exportFields, isLoading: fieldsLoading } =
    useAnnotationQueueExportFields(queueId, { enabled: open && !!queueId });
  const {
    data: datasetOptions = [],
    isLoading: datasetsLoading,
    isFetching: datasetsFetching,
  } = useDevelopDatasetList(datasetSearch, [], {
    enabled: open && mode === "existing",
  });
  const {
    data: existingDatasetColumns = [],
    isLoading: existingColumnsLoading,
  } = useGetDatasetColumns(datasetId, {
    enabled: open && mode === "existing" && Boolean(datasetId),
  });

  const fields = useMemo(() => exportFields?.fields || [], [exportFields]);
  const fieldsById = useMemo(
    () => new Map(fields.map((field) => [field.id, field])),
    [fields],
  );

  const fieldOptions = useMemo(() => [customFieldOption, ...fields], [fields]);
  const datasetColumnOptions = useMemo(
    () =>
      Array.from(
        new Set(
          (existingDatasetColumns || []).map(datasetColumnName).filter(Boolean),
        ),
      ),
    [existingDatasetColumns],
  );

  useEffect(() => {
    if (!open || !exportFields) return;
    const defaultMapping =
      exportFields.default_mapping?.length > 0
        ? exportFields.default_mapping
        : fields.filter((field) => field.default).map(mappingFromField);

    setMapping(
      defaultMapping.map((entry) => {
        const field = fieldsById.get(entry.field);
        const column =
          entry.column || field?.column || customPathFromField(entry.field);
        return {
          field: entry.field,
          column: isAttributeOption(field)
            ? displayAttributePath(column)
            : column,
          enabled: entry.enabled !== false,
        };
      }),
    );
  }, [open, exportFields, fields, fieldsById]);

  useEffect(() => {
    if (mode !== "new") return;
    setDatasetId("");
    setSelectedDataset(null);
    setDatasetSearch("");
  }, [mode]);

  useEffect(() => {
    if (!open) {
      if (exportTimerRef.current) {
        window.clearTimeout(exportTimerRef.current);
        exportTimerRef.current = null;
      }
      setExportSubmitting(false);
    }
  }, [open]);

  useEffect(
    () => () => {
      if (exportTimerRef.current) {
        window.clearTimeout(exportTimerRef.current);
      }
    },
    [],
  );

  const updateMapping = (index, patch) => {
    setMapping((prev) =>
      prev.map((entry, entryIndex) =>
        entryIndex === index ? { ...entry, ...patch } : entry,
      ),
    );
  };

  const handleFieldChange = (index, fieldId) => {
    if (fieldId === CUSTOM_FIELD_VALUE) {
      updateMapping(index, {
        field: "attr:",
        column: mapping[index]?.column || "",
      });
      return;
    }
    const field = fieldsById.get(fieldId);
    if (!field) return;
    if (field.expand_fields?.length) {
      const expandedMappings = field.expand_fields
        .map((expandFieldId) => fieldsById.get(expandFieldId))
        .filter(Boolean)
        .map(mappingFromField);
      if (expandedMappings.length > 0) {
        setMapping((prev) => [
          ...prev.slice(0, index),
          ...expandedMappings,
          ...prev.slice(index + 1),
        ]);
        return;
      }
    }
    updateMapping(index, mappingFromField(field));
  };

  const handleCustomPathChange = (index, path) => {
    const cleanPath = path.trimStart();
    updateMapping(index, {
      field: `attr:${cleanPath}`,
      column: mapping[index]?.column || cleanPath,
    });
  };

  const handleAddColumn = () => {
    setMapping((prev) => [...prev, emptyMapping()]);
  };

  const handleRemoveColumn = (index) => {
    setMapping((prev) => prev.filter((_, entryIndex) => entryIndex !== index));
  };

  const handleMoveColumn = (index, direction) => {
    setMapping((prev) => {
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
  };

  const enabledMapping = mapping.filter((item) => item.enabled);
  const enabledCount = enabledMapping.length;
  const columnNames = enabledMapping
    .map((item) => item.column.trim().toLowerCase())
    .filter(Boolean);
  const hasDuplicateColumns = new Set(columnNames).size !== columnNames.length;
  const hasInvalidEnabledRow = enabledMapping.some(
    (item) => !item.field || item.field === "attr:" || !item.column.trim(),
  );
  const isValid =
    (mode === "new" ? !!datasetName.trim() : !!datasetId.trim()) &&
    enabledCount > 0 &&
    !hasDuplicateColumns &&
    !hasInvalidEnabledRow;
  const exportBusy = exportSubmitting || isPending;
  const drawerBusy = fieldsLoading || exportBusy;

  const handleExport = () => {
    if (!isValid || exportBusy) return;
    setExportSubmitting(true);

    // Yield one tick before preparing/sending the request so the loading panel
    // can paint immediately, even for large mappings.
    exportTimerRef.current = window.setTimeout(() => {
      exportTimerRef.current = null;
      const payload = {
        queueId,
        status_filter: statusFilter,
        column_mapping: mapping
          .filter(
            (item) =>
              item.enabled &&
              item.field &&
              item.field !== "attr:" &&
              item.column.trim(),
          )
          .map((item) => ({
            field: item.field,
            column: item.column.trim(),
            enabled: true,
          })),
      };
      if (mode === "new") {
        payload.dataset_name = datasetName.trim();
      } else {
        payload.dataset_id = datasetId.trim();
      }

      exportToDataset(payload, {
        onSuccess: () => {
          onClose();
          setDatasetName("");
          setDatasetId("");
          setSelectedDataset(null);
          setDatasetSearch("");
          setMode("new");
          setStatusFilter("completed");
        },
        onSettled: () => {
          setExportSubmitting(false);
        },
      });
    }, 0);
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: { xs: "100vw", md: 760 },
          maxWidth: "100vw",
          height: "100vh",
          overflow: "hidden",
        },
      }}
    >
      <Stack
        sx={{ height: "100%", minHeight: 0 }}
        data-testid="export-to-dataset-drawer"
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 2.5, py: 2 }}
        >
          <Typography variant="h6" sx={{ flex: 1 }}>
            Export to Dataset
          </Typography>
          <Tooltip title="Close">
            <IconButton onClick={onClose} aria-label="Close export drawer">
              <Iconify icon="mingcute:close-line" />
            </IconButton>
          </Tooltip>
        </Stack>
        <Divider />
        {drawerBusy && <LinearProgress aria-label="Loading export drawer" />}

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2.5, py: 2 }}>
          <Stack spacing={2.25}>
            {exportBusy && (
              <Stack
                role="status"
                aria-live="polite"
                direction="row"
                spacing={1.5}
                alignItems="center"
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 0.75,
                  bgcolor: "action.hover",
                  px: 1.5,
                  py: 1.25,
                }}
              >
                <CircularProgress
                  color="inherit"
                  size={20}
                  aria-label="Exporting annotation data"
                />
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="subtitle2">
                    Exporting to dataset
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Loading queue data and mapping selected columns. This can
                    take a moment.
                  </Typography>
                </Box>
              </Stack>
            )}

            <RadioGroup
              row
              value={mode}
              onChange={(event) => setMode(event.target.value)}
            >
              <FormControlLabel
                value="new"
                control={<Radio disabled={drawerBusy} />}
                label="Create new dataset"
              />
              <FormControlLabel
                value="existing"
                control={<Radio disabled={drawerBusy} />}
                label="Add to existing dataset"
              />
            </RadioGroup>

            {mode === "new" ? (
              <TextField
                label="Dataset name"
                fullWidth
                size="small"
                value={datasetName}
                onChange={(event) => setDatasetName(event.target.value)}
                disabled={drawerBusy}
                required
              />
            ) : (
              <Autocomplete
                fullWidth
                size="small"
                options={datasetOptions || []}
                value={selectedDataset}
                loading={datasetsLoading || datasetsFetching}
                filterOptions={(options) => options}
                getOptionLabel={datasetOptionLabel}
                isOptionEqualToValue={(option, value) =>
                  datasetOptionId(option) === datasetOptionId(value)
                }
                disabled={drawerBusy}
                onChange={(_, value) => {
                  setSelectedDataset(value);
                  setDatasetId(datasetOptionId(value));
                }}
                onInputChange={(_, value, reason) => {
                  if (reason === "input") setDatasetSearch(value);
                  if (reason === "clear") setDatasetSearch("");
                }}
                noOptionsText={
                  datasetsLoading || datasetsFetching
                    ? "Loading datasets..."
                    : "No datasets found"
                }
                renderOption={(props, option) => {
                  const label = datasetOptionLabel(option);
                  const id = datasetOptionId(option);
                  return (
                    <Box component="li" {...props} key={id} title={label}>
                      <Stack spacing={0.25} sx={{ minWidth: 0 }}>
                        <Typography variant="body2" noWrap>
                          {label}
                        </Typography>
                      </Stack>
                    </Box>
                  );
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label="Dataset"
                    placeholder="Search datasets"
                    required
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <>
                          {datasetsLoading || datasetsFetching ? (
                            <InputAdornment position="end">
                              <CircularProgress size={16} />
                            </InputAdornment>
                          ) : null}
                          {params.InputProps.endAdornment}
                        </>
                      ),
                    }}
                  />
                )}
              />
            )}

            <TextField
              select
              label="Items to export"
              fullWidth
              size="small"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              disabled={drawerBusy}
            >
              {STATUS_OPTIONS.map((opt) => (
                <MenuItem key={opt.value} value={opt.value}>
                  {opt.label}
                </MenuItem>
              ))}
            </TextField>

            <Stack spacing={1}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography variant="subtitle2" sx={{ flex: 1, minWidth: 0 }}>
                  Column Mapping
                </Typography>
                <Chip size="small" label={`${enabledCount} selected`} />
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<Iconify icon="eva:plus-fill" />}
                  onClick={handleAddColumn}
                  disabled={drawerBusy}
                >
                  Add Column
                </Button>
              </Stack>

              {fieldsLoading ? (
                <Stack alignItems="center" sx={{ py: 4 }}>
                  <CircularProgress
                    size={24}
                    aria-label="Loading source fields"
                  />
                </Stack>
              ) : (
                <Stack
                  spacing={1}
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 0.75,
                    p: 1,
                  }}
                >
                  {mapping.map((item, index) => {
                    const knownField = isKnownField(item.field, fieldsById);
                    const sourceValue = knownField
                      ? fieldsById.get(item.field)
                      : customFieldOption;
                    const customPath = customPathFromField(item.field);
                    const duplicateColumn =
                      item.enabled &&
                      item.column.trim() &&
                      columnNames.filter(
                        (name) => name === item.column.trim().toLowerCase(),
                      ).length > 1;

                    return (
                      <Stack
                        key={`${item.field}-${index}`}
                        spacing={1}
                        data-testid="export-mapping-row"
                        sx={{
                          borderBottom:
                            index === mapping.length - 1 ? 0 : "1px solid",
                          borderColor: "divider",
                          pb: index === mapping.length - 1 ? 0 : 1,
                        }}
                      >
                        <Stack
                          direction="row"
                          alignItems="flex-start"
                          spacing={1}
                          useFlexGap
                          flexWrap="wrap"
                          sx={{ minWidth: 0 }}
                        >
                          <Checkbox
                            size="small"
                            checked={item.enabled}
                            disabled={exportBusy}
                            onChange={(event) =>
                              updateMapping(index, {
                                enabled: event.target.checked,
                              })
                            }
                            sx={{ mt: 0.5 }}
                          />
                          <Autocomplete
                            size="small"
                            options={fieldOptions}
                            value={sourceValue}
                            disabled={!item.enabled || exportBusy}
                            disableClearable
                            autoHighlight
                            getOptionLabel={fieldOptionLabel}
                            groupBy={(option) => option.group || "Fields"}
                            isOptionEqualToValue={(option, value) =>
                              option?.id === value?.id
                            }
                            onChange={(_, option) =>
                              handleFieldChange(index, option?.id)
                            }
                            slotProps={{
                              popper: { sx: { zIndex: 1500 } },
                              paper: { sx: { maxHeight: 420 } },
                            }}
                            renderOption={(
                              { key: optionKey, ...optionProps },
                              option,
                            ) => {
                              const description =
                                fieldOptionDescription(option);
                              const label = fieldOptionLabel(option);
                              return (
                                <Box
                                  component="li"
                                  key={optionKey || option.id}
                                  {...optionProps}
                                  title={optionTitle(label, description)}
                                  sx={{
                                    display: "block",
                                    py: 0.75,
                                  }}
                                >
                                  <Typography variant="body2" noWrap>
                                    {label}
                                  </Typography>
                                  {description && (
                                    <Typography
                                      variant="caption"
                                      color="text.secondary"
                                      noWrap
                                      sx={{ display: "block" }}
                                    >
                                      {description}
                                    </Typography>
                                  )}
                                </Box>
                              );
                            }}
                            renderInput={(params) => (
                              <TextField
                                {...params}
                                label="Source field"
                                placeholder="Search fields"
                              />
                            )}
                            sx={{ minWidth: 0, flex: "1 1 220px" }}
                          />
                          {mode === "existing" ? (
                            <Autocomplete
                              freeSolo
                              size="small"
                              options={datasetColumnOptions}
                              value={item.column || ""}
                              inputValue={item.column || ""}
                              disabled={!item.enabled || exportBusy}
                              loading={existingColumnsLoading}
                              openOnFocus
                              forcePopupIcon
                              selectOnFocus
                              noOptionsText={
                                datasetId
                                  ? "No existing columns. Type a new column."
                                  : "Select a dataset first"
                              }
                              slotProps={{
                                popper: { sx: { zIndex: 1500 } },
                              }}
                              renderOption={(props, option) => {
                                const label = datasetColumnName(option);
                                return (
                                  <Box
                                    component="li"
                                    {...props}
                                    key={label}
                                    title={label}
                                  >
                                    <Typography variant="body2" noWrap>
                                      {label}
                                    </Typography>
                                  </Box>
                                );
                              }}
                              onChange={(_, value) =>
                                updateMapping(index, {
                                  column: datasetColumnName(value),
                                })
                              }
                              onInputChange={(_, value, reason) => {
                                if (reason === "input" || reason === "clear") {
                                  updateMapping(index, { column: value });
                                }
                              }}
                              renderInput={(params) => (
                                <TextField
                                  {...params}
                                  label="Dataset column"
                                  placeholder="Select existing or type new"
                                  error={Boolean(duplicateColumn)}
                                  helperText={
                                    duplicateColumn ? "Duplicate column" : ""
                                  }
                                  InputProps={{
                                    ...params.InputProps,
                                    endAdornment: (
                                      <>
                                        {existingColumnsLoading ? (
                                          <InputAdornment position="end">
                                            <CircularProgress size={16} />
                                          </InputAdornment>
                                        ) : null}
                                        {params.InputProps.endAdornment}
                                      </>
                                    ),
                                  }}
                                />
                              )}
                              sx={{ minWidth: 0, flex: "1 1 180px" }}
                            />
                          ) : (
                            <TextField
                              size="small"
                              label="Dataset column"
                              value={item.column}
                              disabled={!item.enabled || exportBusy}
                              onChange={(event) =>
                                updateMapping(index, {
                                  column: event.target.value,
                                })
                              }
                              error={Boolean(duplicateColumn)}
                              helperText={
                                duplicateColumn ? "Duplicate column" : ""
                              }
                              sx={{ minWidth: 0, flex: "1 1 180px" }}
                            />
                          )}
                          <Tooltip title="Move up">
                            <span>
                              <IconButton
                                size="small"
                                disabled={exportBusy || index === 0}
                                onClick={() => handleMoveColumn(index, -1)}
                                aria-label="Move column up"
                              >
                                <Iconify icon="eva:arrow-ios-upward-fill" />
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Tooltip title="Move down">
                            <span>
                              <IconButton
                                size="small"
                                disabled={
                                  exportBusy || index === mapping.length - 1
                                }
                                onClick={() => handleMoveColumn(index, 1)}
                                aria-label="Move column down"
                              >
                                <Iconify icon="eva:arrow-ios-downward-fill" />
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Tooltip title="Remove">
                            <span>
                              <IconButton
                                size="small"
                                onClick={() => handleRemoveColumn(index)}
                                aria-label="Remove column"
                                disabled={exportBusy}
                              >
                                <Iconify icon="eva:trash-2-outline" />
                              </IconButton>
                            </span>
                          </Tooltip>
                        </Stack>
                        {!knownField && (
                          <TextField
                            size="small"
                            label="Attribute path"
                            value={customPath}
                            disabled={!item.enabled || exportBusy}
                            onChange={(event) =>
                              handleCustomPathChange(index, event.target.value)
                            }
                            placeholder="span_attributes.customer.tier"
                            sx={{ ml: { xs: 0, sm: 5.5 }, width: "100%" }}
                          />
                        )}
                      </Stack>
                    );
                  })}
                </Stack>
              )}
            </Stack>
          </Stack>
        </Box>

        <Divider />
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          useFlexGap
          flexWrap="wrap"
          sx={{ p: 2 }}
        >
          <Button onClick={onClose} disabled={exportBusy}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleExport}
            disabled={exportBusy || fieldsLoading || !isValid}
            startIcon={
              exportBusy ? <CircularProgress color="inherit" size={16} /> : null
            }
          >
            {exportBusy ? "Exporting..." : "Export"}
          </Button>
        </Stack>
      </Stack>
    </Drawer>
  );
}

ExportToDatasetDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  queueId: PropTypes.string.isRequired,
};
