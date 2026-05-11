import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
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

const isKnownField = (field, fieldsById) => Boolean(fieldsById.get(field));

const isAttributeOption = (option) => option?.id?.startsWith("attr:");

const fieldOptionLabel = (option) => {
  if (!option) return "";
  if (isAttributeOption(option)) {
    return (
      option.path || attributePathFromField(option.id) || option.label || ""
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
  column: field.column || field.id,
  enabled: true,
});

const customFieldOption = {
  id: CUSTOM_FIELD_VALUE,
  label: "Custom attribute path",
  group: "Custom",
};

export default function ExportToDatasetDialog({ open, onClose, queueId }) {
  const [mode, setMode] = useState("new");
  const [datasetName, setDatasetName] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [statusFilter, setStatusFilter] = useState("completed");
  const [mapping, setMapping] = useState([]);

  const { mutate: exportToDataset, isPending } = useExportToDataset();
  const { data: exportFields, isLoading: fieldsLoading } =
    useAnnotationQueueExportFields(queueId, { enabled: open && !!queueId });

  const fields = useMemo(() => exportFields?.fields || [], [exportFields]);
  const fieldsById = useMemo(
    () => new Map(fields.map((field) => [field.id, field])),
    [fields],
  );

  const fieldOptions = useMemo(() => [customFieldOption, ...fields], [fields]);

  useEffect(() => {
    if (!open || !exportFields) return;
    const defaultMapping =
      exportFields.default_mapping?.length > 0
        ? exportFields.default_mapping
        : fields.filter((field) => field.default).map(mappingFromField);

    setMapping(
      defaultMapping.map((entry) => {
        const field = fieldsById.get(entry.field);
        return {
          field: entry.field,
          column:
            entry.column || field?.column || customPathFromField(entry.field),
          enabled: entry.enabled !== false,
        };
      }),
    );
  }, [open, exportFields, fields, fieldsById]);

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
  const drawerBusy = fieldsLoading || isPending;

  const handleExport = () => {
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
        setMode("new");
        setStatusFilter("completed");
      },
    });
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
              <TextField
                label="Dataset ID"
                fullWidth
                size="small"
                value={datasetId}
                onChange={(event) => setDatasetId(event.target.value)}
                disabled={drawerBusy}
                required
                placeholder="Paste dataset UUID"
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
                <Typography variant="subtitle2" sx={{ flex: 1 }}>
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
                        >
                          <Checkbox
                            size="small"
                            checked={item.enabled}
                            disabled={isPending}
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
                            disabled={!item.enabled || isPending}
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
                              return (
                                <Box
                                  component="li"
                                  key={optionKey || option.id}
                                  {...optionProps}
                                  sx={{
                                    display: "block",
                                    py: 0.75,
                                  }}
                                >
                                  <Typography variant="body2" noWrap>
                                    {fieldOptionLabel(option)}
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
                            sx={{ minWidth: 220, flex: 1.25 }}
                          />
                          <TextField
                            size="small"
                            label="Dataset column"
                            value={item.column}
                            disabled={!item.enabled || isPending}
                            onChange={(event) =>
                              updateMapping(index, {
                                column: event.target.value,
                              })
                            }
                            error={Boolean(duplicateColumn)}
                            helperText={
                              duplicateColumn ? "Duplicate column" : ""
                            }
                            sx={{ minWidth: 180, flex: 1 }}
                          />
                          <Tooltip title="Move up">
                            <span>
                              <IconButton
                                size="small"
                                disabled={isPending || index === 0}
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
                                  isPending || index === mapping.length - 1
                                }
                                onClick={() => handleMoveColumn(index, 1)}
                                aria-label="Move column down"
                              >
                                <Iconify icon="eva:arrow-ios-downward-fill" />
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Tooltip title="Remove">
                            <IconButton
                              size="small"
                              onClick={() => handleRemoveColumn(index)}
                              aria-label="Remove column"
                              disabled={isPending}
                            >
                              <Iconify icon="eva:trash-2-outline" />
                            </IconButton>
                          </Tooltip>
                        </Stack>
                        {!knownField && (
                          <TextField
                            size="small"
                            label="Attribute path"
                            value={customPath}
                            disabled={!item.enabled || isPending}
                            onChange={(event) =>
                              handleCustomPathChange(index, event.target.value)
                            }
                            placeholder="span_attributes.customer.tier"
                            sx={{ ml: 5.5 }}
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
          sx={{ p: 2 }}
        >
          <Button onClick={onClose} disabled={isPending}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleExport}
            disabled={isPending || fieldsLoading || !isValid}
            startIcon={
              isPending ? <CircularProgress color="inherit" size={16} /> : null
            }
          >
            {isPending ? "Exporting..." : "Export"}
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
