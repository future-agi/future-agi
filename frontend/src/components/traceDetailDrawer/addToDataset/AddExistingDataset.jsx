import React, { useState, useEffect } from "react";
import {
  Typography,
  Box,
  IconButton,
  Grid,
  TextField,
  Checkbox,
  Button,
  MenuItem,
  useTheme,
  FormControl,
  Skeleton,
} from "@mui/material";
import Iconify from "../../iconify";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import { useNavigate, useParams } from "react-router";
import FormSearchSelectFieldState from "src/components/FromSearchSelectField/FormSearchSelectFieldState";
import { LLM_TABS } from "../../../sections/projects/LLMTracing/common";

const AddExistingDataset = ({
  handleclose,
  selectedNode,
  availableDatasets,
  observationFields,
  selectedTraces,
  selectedSpans,
  selectAll,
  currentTab,
  onSuccess,
}) => {
  const [selectedDataset, setSelectedDataset] = useState(null);
  const [allColumnOption, setAllColumnOption] = useState([]);
  const [mapToColumn, setMapToColumn] = useState([]);
  const [showInputIndex, setShowInputIndex] = useState(null);
  const [newColumnName, setNewColumnName] = useState("");
  const [editingColumnId, setEditingColumnId] = useState(null);
  const { observeId } = useParams();

  const theme = useTheme();
  const navigate = useNavigate();

  const handleColumnChange = (index, column) => {
    const updatedMap = [...mapToColumn];
    updatedMap[index].id = column.id;
    updatedMap[index].column = column.name;
    setMapToColumn(updatedMap);
  };

  const handleCheckboxChange = (index) => {
    const updated = [...mapToColumn];
    updated[index].checked = !updated[index].checked;
    updated.sort((a, b) => {
      if (a.checked === b.checked) return 0;
      return a.checked ? -1 : 1;
    });
    setNewColumnName("");
    setShowInputIndex(null);

    setMapToColumn(updated);
  };

  const isMenuItemDisabled = (col, item) => {
    const isUsedButNotSame = col.used && col.name !== item.column;

    const isTypeCompatible =
      col.type === item.type ||
      (item.type === "text" &&
        (col.type === "text" || col.type === "string")) ||
      (item.type === "string" &&
        (col.type === "text" || col.type === "string")) ||
      (item.type === "integer" &&
        (col.type === "integer" || col.type === "float")) ||
      (item.type === "float" &&
        (col.type === "float" || col.type === "integer"));

    return isUsedButNotSame || !isTypeCompatible;
  };

  useEffect(() => {
    if (!mapToColumn?.length) return;

    const usedColumns = mapToColumn
      .filter((item) => item.column)
      .map((item) => item.column);

    setAllColumnOption((currentOptions) => {
      if (!currentOptions?.length) return currentOptions;

      let hasChanges = false;
      const updatedOptions = currentOptions.map((col) => {
        const used = usedColumns.includes(col.name);
        if (col.used === used) return col;
        hasChanges = true;
        return { ...col, used };
      });

      return hasChanges ? updatedOptions : currentOptions;
    });
  }, [mapToColumn]);

  useEffect(() => {
    if (selectedDataset) {
      setAllColumnOption([]);
      setMapToColumn([]);
      setNewColumnName("");
      setShowInputIndex(null);
    }
  }, [selectedDataset]);

  const isDuplicateColumnName = (name) => {
    const trimmedName = name.trim().toLowerCase();
    if (!trimmedName) return false;

    return allColumnOption.some(
      (col) => col.name.trim().toLowerCase() === trimmedName,
    );
  };
  const handleAddOrEditColumn = (name, index) => {
    const trimmedName = name.trim();
    if (!trimmedName) return;

    if (isDuplicateColumnName(trimmedName)) {
      return;
    }

    if (editingColumnId !== null) {
      // === EDIT COLUMN ===
      updateColumn({
        name,
        index,
      });
    } else {
      // === ADD NEW COLUMN ===
      addColumn({
        name,
        index,
      });
    }
  };

  const updateColumn = ({ name, index }) => {
    const updatedOptions = allColumnOption.map((col, key) =>
      key === editingColumnId ? { ...col, name: name } : col,
    );
    setAllColumnOption(updatedOptions);
    handleColumnChange(index, {
      name: name,
      id: "",
    });

    setShowInputIndex(null);
    setNewColumnName("");
    setEditingColumnId(null);
  };

  const addColumn = ({ name, index }) => {
    const newCol = {
      name: name,
      id: "",
      type: mapToColumn[index]?.type || "text",
      used: true,
      editable: true,
    };

    const updatedColumns = [...allColumnOption, newCol];
    setAllColumnOption(updatedColumns);
    handleColumnChange(index, newCol);

    setShowInputIndex(null);
    setNewColumnName("");
    setEditingColumnId(null);
  };

  const { data, isLoading: newColumnAdded } = useQuery({
    queryKey: ["datasetById", selectedDataset],
    enabled: Boolean(selectedDataset),
    queryFn: () =>
      axios
        .get(endpoints.develop.getDatasetColumns(selectedDataset))
        .then((res) => res.data),
    select: (data) => data?.result,
  });

  useEffect(() => {
    const columnsWithUsedFlag = data?.columns.map((col) => ({
      id: col.id,
      name: col.name,
      type: col.data_type ?? col.dataType,
      used: false,
    }));

    setAllColumnOption(columnsWithUsedFlag);

    const updatedMapping = observationFields.map((field) => {
      const isInColumn = data?.columns.find(
        (col) =>
          col.name === field.name &&
          (col.data_type ?? col.dataType) === field.type,
      );
      return {
        ...field,
        column: isInColumn ? field.name : "",
        id: isInColumn ? isInColumn.id : "",
        checked: true,
      };
    });

    // Sort: checked fields first
    updatedMapping.sort((a, b) => {
      if (a.checked === b.checked) return 0;
      return a.checked ? -1 : 1;
    });

    setMapToColumn(updatedMapping);
  }, [data?.columns, observationFields]);

  const handleAddToDataset = () => {
    const selected = mapToColumn.filter(
      (item) => item.checked && item.column.trim() !== "",
    );

    const mapping_config = [];
    const new_mapping_config = [];

    selected.forEach((item) => {
      const mappedItem = {
        col_name: item.column,
        data_type: item.type,
        span_field: item.name,
      };

      if (item.id && item.id.trim() !== "") {
        mapping_config.push(mappedItem);
      } else {
        new_mapping_config.push(mappedItem);
      }
    });

    addToDatasetMutation({
      dataset_id: selectedDataset,
      mapping_config,
      new_mapping_config,
      select_all: Boolean(selectAll),
      project: observeId,
      ...(currentTab === LLM_TABS.TRACE
        ? {
            trace_ids: selectedTraces,
          }
        : {
            span_ids: selectedNode ? [selectedNode] : selectedSpans,
          }),
    });
  };

  const { mutate: addToDatasetMutation, isPending: isAdding } = useMutation({
    mutationFn: (payload) =>
      axios.post(endpoints.project.addExistingDataset, payload),
    onSuccess: (res) => {
      if (res.data?.status) {
        enqueueSnackbar(
          <>
            <span>Data added successfully</span>
            {/* {res.data.result.message} */}
            <span
              onClick={() =>
                navigate(`/dashboard/develop/${selectedDataset}?tab=data`, {
                  state: { from: location.pathname },
                })
              }
              style={{
                textDecoration: "underline",
                cursor: "pointer",
                color: theme.palette.green[500],
                fontWeight: "500",
                marginLeft: "10px",
              }}
            >
              View Dataset
            </span>
          </>,
          { variant: "success" },
        );
        handleclose();
        if (onSuccess) {
          onSuccess();
        }
      }
    },
  });

  return (
    <>
      <FormSearchSelectFieldState
        fullWidth
        label="Dataset"
        placeholder="Select Dataset"
        onChange={(e) => setSelectedDataset(e.target.value)}
        value={selectedDataset}
        defaultValue={selectedDataset}
        options={availableDatasets?.map((dataset) => ({
          label: dataset.name,
          value: dataset.dataset_id ?? dataset.datasetId,
        }))}
        size="small"
      />

      {/* Mapping Section */}
      <Typography
        variant="body2"
        fontWeight={500}
        mt={2}
        mb={1}
        color={theme.palette.text.primary}
      >
        Map to column
      </Typography>

      <Box
        sx={{
          border: `1px solid ${theme.palette.divider}`,
          borderRadius: "8px",
          px: 1,
          py: 2,
          height: "100%",
          overflowY: "auto",
        }}
      >
        {selectedDataset ? (
          !newColumnAdded ? (
            mapToColumn.map((item, index) => (
              <Grid
                container
                alignItems="flex-start"
                mb={2}
                key={index}
                wrap="wrap"
                spacing={1}
              >
                <Grid item xs={12} sm={5}>
                  <Box display="flex" alignItems="center">
                    <Checkbox
                      onChange={() => handleCheckboxChange(index)}
                      checked={item.checked}
                      sx={{ marginRight: "5px" }}
                      icon={
                        <Iconify
                          icon="system-uicons:checkbox-empty"
                          color="text.disabled"
                          width="20px"
                          height="20px"
                        />
                      }
                      checkedIcon={
                        <Iconify
                          icon="famicons:checkbox"
                          color="primary.light"
                          width="20px"
                          height="20px"
                        />
                      }
                    />
                    <TextField
                      fullWidth
                      value={item.name}
                      size="small"
                      disabled
                    />
                  </Box>
                </Grid>
                {/* Arrow */}
                <Grid
                  item
                  xs={12}
                  sm={2}
                  sx={{ textAlign: "center", mt: "16px" }}
                >
                  <Box display="flex" alignItems="center" sx={{ px: 2 }}>
                    <Box
                      sx={{
                        width: 8,
                        height: 8,
                        bgcolor: theme.palette.divider,
                        borderRadius: "50%",
                      }}
                    />

                    <Box
                      sx={{
                        flexGrow: 1,
                        height: "1px",
                        bgcolor: theme.palette.divider,
                        position: "relative",
                      }}
                    >
                      <Box
                        sx={{
                          position: "absolute",
                          right: 0,
                          top: "50%",
                          transform: "translateY(-50%)",
                          width: 0,
                          height: 0,
                          borderTop: "4px solid transparent",
                          borderBottom: "4px solid transparent",
                          borderLeft: `6px solid ${theme.palette.divider}`,
                        }}
                      />
                    </Box>
                  </Box>
                </Grid>
                <Grid item xs={12} sm={5}>
                  <Box>
                    <FormControl size="small" fullWidth>
                      <FormSearchSelectFieldState
                        size="small"
                        label="Column"
                        value={item?.column ?? ""}
                        sx={{ width: "100%" }}
                        placeholder="Select column"
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === "add_column") {
                            setShowInputIndex(index);
                            setNewColumnName("");
                          } else {
                            handleColumnChange(index, value);
                          }
                        }}
                        options={[
                          ...(Array.isArray(allColumnOption)
                            ? allColumnOption
                            : []
                          ).map((col, i) => ({
                            label: col?.name,
                            value: col,
                            component: (
                              <MenuItem
                                key={i}
                                disabled={isMenuItemDisabled(col, item)}
                                sx={{
                                  display: "flex",
                                  justifyContent: "space-between",
                                  alignItems: "center",
                                  color: theme.palette.text.primary,
                                  "&.Mui-disabled": {
                                    color: theme.palette.divider,
                                  },
                                }}
                              >
                                {col.name}
                                {col.editable && (
                                  <IconButton
                                    size="small"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setShowInputIndex(index);
                                      setNewColumnName(col.name);
                                      setEditingColumnId(i);
                                    }}
                                  >
                                    <Iconify
                                      icon="fluent:edit-16-regular"
                                      width={14}
                                      height={14}
                                    />
                                  </IconButton>
                                )}
                              </MenuItem>
                            ),
                          })),
                          {
                            label: "+ Add Column",
                            value: "add_column",
                            alwaysVisible: true,
                            component: (
                              <MenuItem key="add_column">
                                <Box sx={{ color: "primary.main" }}>
                                  + Add Column
                                </Box>
                              </MenuItem>
                            ),
                          },
                        ]}
                      />
                      {showInputIndex === index && (
                        <Box mt={1}>
                          <TextField
                            fullWidth
                            size="small"
                            placeholder="Enter new column name"
                            value={newColumnName}
                            onChange={(e) => {
                              setNewColumnName(e.target.value);
                            }}
                            error={
                              editingColumnId === null &&
                              isDuplicateColumnName(newColumnName)
                            }
                            helperText={
                              editingColumnId === null &&
                              isDuplicateColumnName(newColumnName)
                                ? "Column name already exists"
                                : ""
                            }
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                handleAddOrEditColumn(newColumnName, index);
                              }
                            }}
                            InputProps={{
                              endAdornment: (
                                <IconButton
                                  size="small"
                                  onClick={() =>
                                    handleAddOrEditColumn(newColumnName, index)
                                  }
                                  edge="end"
                                >
                                  <Iconify
                                    icon="tdesign:enter"
                                    width="12"
                                    height="12"
                                  />
                                </IconButton>
                              ),
                            }}
                          />
                        </Box>
                      )}
                    </FormControl>
                  </Box>
                </Grid>
              </Grid>
            ))
          ) : (
            Array.from({ length: 9 }).map((_, index) => (
              <Grid
                container
                alignItems="flex-start"
                mb={2}
                key={index}
                wrap="wrap"
                spacing={1}
              >
                <Grid item xs={12} sm={5}>
                  <Box display="flex" alignItems="center">
                    <Skeleton
                      variant="circular"
                      width={20}
                      height={20}
                      sx={{ mr: 1 }}
                    />
                    <Skeleton variant="rectangular" height={36} width="100%" />
                  </Box>
                </Grid>
                <Grid
                  item
                  xs={12}
                  sm={2}
                  sx={{ textAlign: "center", mt: "16px" }}
                >
                  <Box display="flex" alignItems="center" sx={{ px: 2 }}>
                    <Box
                      sx={{
                        width: 8,
                        height: 8,
                        bgcolor: theme.palette.divider,
                        borderRadius: "50%",
                      }}
                    />

                    <Box
                      sx={{
                        flexGrow: 1,
                        height: "1px",
                        bgcolor: theme.palette.divider,
                        position: "relative",
                      }}
                    >
                      <Box
                        sx={{
                          position: "absolute",
                          right: 0,
                          top: "50%",
                          transform: "translateY(-50%)",
                          width: 0,
                          height: 0,
                          borderTop: "4px solid transparent",
                          borderBottom: "4px solid transparent",
                          borderLeft: `6px solid ${theme.palette.divider}`,
                        }}
                      />
                    </Box>
                  </Box>
                  {/* </Grid> */}
                </Grid>
                <Grid item xs={12} sm={5}>
                  <Skeleton variant="rectangular" height={36} />
                </Grid>
              </Grid>
            ))
          )
        ) : (
          <Box
            sx={{
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Typography variant="body2" color={theme.palette.text.secondary}>
              Choose a dataset to perform column mapping
            </Typography>
          </Box>
        )}
      </Box>

      {/* Footer Buttons */}
      <Box mt={2} display="flex" gap={2}>
        <Button
          variant="outlined"
          fullWidth
          sx={{
            fontSize: "14px",
            fontWeight: 500,
            color: theme.palette.text.secondary,
            borderColor: theme.palette.divider,
          }}
          onClick={handleclose}
        >
          Cancel
        </Button>
        <LoadingButton
          variant="contained"
          fullWidth
          color="primary"
          loading={isAdding}
          disabled={
            !selectedDataset ||
            !mapToColumn.some((f) => f.checked) ||
            mapToColumn.some((f) => f.checked && f.column?.trim() === "")
          }
          onClick={handleAddToDataset}
          sx={{
            fontSize: "14px",
            fontWeight: 600,
            "&.Mui-disabled": {
              bgcolor: theme.palette.divider,
              color: theme.palette.background.paper,
            },
          }}
        >
          Add to dataset
        </LoadingButton>
      </Box>
    </>
  );
};

AddExistingDataset.propTypes = {
  handleclose: PropTypes.func,
  selectedNode: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  availableDatasets: PropTypes.array,
  observationFields: PropTypes.array,
  selectedTraces: PropTypes.array,
  selectedSpans: PropTypes.array,
  selectAll: PropTypes.bool,
  currentTab: PropTypes.string,
  onSuccess: PropTypes.func,
};

export default AddExistingDataset;
