import React, { useMemo, useState, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  FormControl,
  FormControlLabel,
  InputAdornment,
  InputLabel,
  ListSubheader,
  MenuItem,
  Select,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { useSnackbar } from "notistack";

import PromptDialog from "../../PromptDialog/PromptDialog";

import DrawerHeaderbar from "./DrawerHeaderbar";

const ImportDataset = (props) => {
  const { onClose, variables, setExtractedVars, setAppliedVariableData } =
    props;

  // dev only
  const [confirmationDialoag, setConfirmationDialog] = useState(false);
  const [datasetId, setDataSetId] = useState(null);
  const [mappingData, setMappingData] = useState([]);
  const [datafromDataset, setDatafromDataset] = useState([]);
  const [dropdownValues2] = useState({});
  const [selectedVariables, setSelectedVariables] = useState({});
  const [searchQuery, setSearchQuery] = useState("");
  const [columns, setColumns] = useState({});
  const { enqueueSnackbar } = useSnackbar();
  const datasetDetail = useQuery({
    queryKey: ["datasetDetail", datasetId],
    queryFn: () => axios.get(endpoints.develop.getDatasetDetail(datasetId), {}),
    enabled: !!datasetId, // This is added to avoid useQuery hook from carelessly firing when datasetId is not set
  });
  const { data, isLoading } = useQuery({
    queryKey: ["datasets"],
    queryFn: async () => {
      const res = await axios.get(endpoints.develop.getDatasets());
      return res.data;
    },
  });

  const datasetOptions = useMemo(() => {
    let datasetInfo = [];
    if (!isLoading) {
      datasetInfo = data?.result?.datasets;
    }
    return datasetInfo?.filter((item) =>
      item?.name.toLowerCase().includes(searchQuery.toLowerCase()),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, data]);

  useEffect(() => {
    if (datasetDetail.data) {
      const fetchedColumns =
        datasetDetail?.data?.data?.result?.columnConfig || [];
      const fetchedData = datasetDetail?.data?.data?.result?.table || [];
      const fetchedColumnsObject = {};
      fetchedColumns.map((col) => {
        fetchedColumnsObject[col.id] = col.name;
      });
      setColumns(fetchedColumnsObject);
      setDatafromDataset(fetchedData);
      setMappingData(
        fetchedColumns.map(() => {
          return {
            datasetKey: "",
            datasetKeyId: "",
            variableKey: "",
          };
        }),
      );
    }
  }, [datasetDetail.data]);

  useEffect(() => {
    if (datasetDetail.error) {
      setColumns([]);
      setMappingData([]);
    }
  }, [datasetDetail.error]);

  const handleCheckboxChange = (event) => {
    if (event.target.checked) {
      setExtractedVars((prev) => [...prev, "Untitled Variable"]);
    } else {
      setExtractedVars((prev) =>
        prev.filter((item) => item.name !== "Untitled Variable"),
      );
    }
  };

  const handleColumnChange = (ind, colId) => {
    setMappingData((prev) => {
      const updatedMapping = [...prev];
      updatedMapping[ind].datasetKey = columns[colId];
      updatedMapping[ind].datasetKeyId = colId;
      return updatedMapping;
    });
  };

  const handleColumn2Change = () => {};

  const handleVariableChange = (ind, variableValue) => {
    setSelectedVariables((prev) => {
      const updatedVariables = { ...prev };

      // Remove the variable from any other mappings
      Object.keys(updatedVariables).forEach((key) => {
        if (updatedVariables[key] === variableValue && parseInt(key) !== ind) {
          updatedVariables[key] = "";
        }
      });

      updatedVariables[ind] = variableValue;
      return updatedVariables;
    });

    setMappingData((prev) => {
      const updatedMapping = [...prev];
      updatedMapping[ind].variableKey = variableValue;
      return updatedMapping;
    });
  };

  const handleChange = async (event) => {
    const datasetId = event.target.value;
    setDataSetId(datasetId);
  };

  // const queryClient = useQueryClient();

  // const { mutate: applyVariables } = useMutation({
  //   mutationFn: (body) =>
  //     axios.post(endpoints.develop.runPrompt.applyVariables(), body),
  //   onSuccess: (data, variables) => {
  //     enqueueSnackbar("Variables applied successfully", { variant: "success" });
  //     setVariablesData(data?.data?.result?.result);
  //     queryClient.invalidateQueries({ queryKey: ["variables"] });
  //     onClose();
  //   },
  //   onError: (error) => {
  //     enqueueSnackbar("Failed to apply variables", { variant: "error" });
  //   },
  // });

  const handleSubmitVariable = () => {
    if (!datasetId) {
      enqueueSnackbar("Dataset ID is required", { variant: "error" });
      return;
    }
    const completeMappings = mappingData.filter((mapping) => {
      return mapping.datasetKeyId != "" && mapping.variableKey != "";
    });
    const incompleteMappings = mappingData.filter((mapping) => {
      return (
        (mapping.datasetKeyId == "" && mapping.variableKey != "") ||
        (mapping.datasetKeyId != "" && mapping.variableKey == "")
      );
    });
    if (incompleteMappings.length > 0) {
      enqueueSnackbar("Please map all the columns to variables", {
        variant: "error",
      });
      return;
    }

    const dataFormat = {};
    const selectedVariablesArray = Object.values(selectedVariables);
    selectedVariablesArray.forEach((variable) => {
      dataFormat[variable] = [];
    });

    const nameIdMapping = {};
    completeMappings.forEach((col) => {
      nameIdMapping[col.variableKey] = col.datasetKeyId;
    });

    for (let i = 0; i < datafromDataset.length; i++) {
      for (let j = 0; j < selectedVariablesArray.length; j++) {
        const variable = selectedVariablesArray[j];
        const mapping = nameIdMapping[variable];
        const celValue = datafromDataset[i][mapping]?.cellValue;
        if (celValue && variable) {
          dataFormat[variable].push(celValue);
        }
      }
    }
    const newAppliedVariableData = {};
    for (let i = 0; i < variables.length; i++) {
      newAppliedVariableData[variables[i]] = new Array(
        datafromDataset.length,
      ).fill("");
    }
    const updatedAppliedVariableData = {
      ...newAppliedVariableData,
      ...dataFormat,
    };
    setAppliedVariableData(updatedAppliedVariableData);
    enqueueSnackbar("Variables applied successfully", { variant: "success" });
    onClose();
  };

  return (
    <Box
      sx={{
        padding: "16px 0 20px 0",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        flex: "1",
      }}
    >
      <DrawerHeaderbar title="Import from dataset" onClose={onClose} />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          height: "calc(100% - 87px)",
          flex: "1",
        }}
      >
        {/* subtitle */}
        <Box
          sx={{
            display: "flex",
            gap: 0.5,
            alignItems: "center",
            padding: "0 16px",
          }}
        >
          <Iconify icon="solar:info-circle-bold" color="text.disabled" />
          <Typography variant="caption" color="text.secondary">
            Choose Dataset to assign its column values to the variables
          </Typography>
        </Box>

        {/* dropdown */}
        <Box sx={{ padding: "0 16px", margin: "30px 0" }}>
          <FormControl size="small" fullWidth>
            <InputLabel id="system-selector">Dataset</InputLabel>
            <Select
              value={datasetId}
              onChange={handleChange}
              MenuProps={{ autoFocus: false }}
              // displayEmpty
              // labelId="system-selector"
            >
              <ListSubheader
                sx={{
                  px: 0,
                  py: 0,
                  bgcolor: "transparent",
                }}
              >
                <TextField
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify
                          icon="eva:search-fill"
                          sx={{ color: "text.disabled" }}
                        />
                      </InputAdornment>
                    ),
                  }}
                  fullWidth
                  size="small"
                  autoFocus
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Escape") {
                      // Prevents autoselecting item while typing (default Select behaviour)
                      e.stopPropagation();
                    }
                  }}
                />
              </ListSubheader>
              {datasetOptions?.map((dataset) => (
                <MenuItem key={dataset?.id} value={dataset?.id}>
                  {dataset?.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>

        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            flex: "1",
            margin: "0 16px",
            height: "100%",
            borderRadius: "8px",
            overflow: "auto",
          }}
        >
          <Typography
            variant="body2"
            fontWeight="fontWeightBold"
            color="text.disabled"
            sx={{ margin: "0 0 16px 0" }}
          >
            Map column to variable
          </Typography>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              padding: "18px",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              overflow: "auto",
              gap: "25px",
              height: "100%",
              flex: 1,
            }}
          >
            {(variables.length > Object.keys(columns).length
              ? Object.keys(columns)
              : Object.keys(columns).slice(0, variables.length)
            ).map((vl, ind) => (
              <Box
                key={ind}
                sx={{
                  display: "grid",
                  gap: "12px",
                  gridTemplateColumns: "1fr 90px 1fr",
                }}
              >
                {/* Column Selector */}
                <FormControl size="small" fullWidth>
                  <InputLabel id={`column-selector-${ind}`}>Column</InputLabel>
                  <Select
                    labelId={`column-selector-${ind}`}
                    id={`column-selector-${ind}`}
                    value={mappingData[ind].datasetKeyId || ""}
                    label="Column"
                    onChange={(e) => handleColumnChange(ind, e.target.value)}
                  >
                    {Object.keys(columns).map((col) => (
                      <MenuItem key={col} value={col}>
                        {columns[col]}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                {/* Line */}
                <Box sx={{ display: "flex", alignItems: "center" }}>
                  <svg
                    width="90"
                    height="9"
                    viewBox="0 0 90 9"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                  >
                    <path
                      d="M0.533334 4.35156C0.533334 6.26615 2.08541 7.81823 4 7.81823C5.91459 7.81823 7.46667 6.26615 7.46667 4.35156C7.46667 2.43698 5.91459 0.884896 4 0.884896C2.08541 0.884896 0.533334 2.43698 0.533334 4.35156ZM90 4.35156L83.5 0.598786V8.10434L90 4.35156ZM4 5.00156H84.15V3.70156H4V5.00156Z"
                      fill="divider"
                    />
                  </svg>
                </Box>

                {/* Variable Selector */}
                <FormControl size="small" fullWidth>
                  <InputLabel id={`variable-selector-${ind}`}>
                    Variable
                  </InputLabel>
                  <Select
                    labelId={`variable-selector-${ind}`}
                    id={`variable-selector-${ind}`}
                    value={mappingData[ind]?.variableKey || ""}
                    label="Variable"
                    onChange={(e) => handleVariableChange(ind, e.target.value)}
                  >
                    {variables
                      .filter(
                        (variable) =>
                          !Object.values(selectedVariables).includes(
                            variable,
                          ) || selectedVariables[ind] === variable,
                      )
                      .map((variable) => (
                        <MenuItem key={variable} value={variable}>
                          {variable}
                        </MenuItem>
                      ))}
                  </Select>
                </FormControl>
              </Box>
            ))}
            {(() => {
              const loopCount = Object.keys(columns).length - variables.length;
              const elements = [];
              for (let i = 0; i < loopCount; i++) {
                elements.push(
                  <Box
                    key={i}
                    sx={{
                      display: "grid",
                      gap: "12px",
                      gridTemplateColumns: "1fr 90px 1fr",
                    }}
                  >
                    <FormControl size="small" fullWidth>
                      <InputLabel id="system-selector">Column</InputLabel>
                      <Select
                        labelId="system-selector-label"
                        id="system-selector"
                        value={dropdownValues2[i] || ""}
                        label="System"
                        onChange={(e) => handleColumn2Change(i, e.target.value)}
                      >
                        {Object.keys(columns).map((col) => (
                          <MenuItem key={col} value={col}>
                            {columns[col]}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>

                    <Box sx={{ display: "flex", alignItems: "center" }}>
                      <svg
                        width="90"
                        height="9"
                        viewBox="0 0 90 9"
                        fill="none"
                        xmlns="http://www.w3.org/2000/svg"
                      >
                        <path
                          d="M0.533334 4.35156C0.533334 6.26615 2.08541 7.81823 4 7.81823C5.91459 7.81823 7.46667 6.26615 7.46667 4.35156C7.46667 2.43698 5.91459 0.884896 4 0.884896C2.08541 0.884896 0.533334 2.43698 0.533334 4.35156ZM90 4.35156L83.5 0.598786V8.10434L90 4.35156ZM4 5.00156H84.15V3.70156H4V5.00156Z"
                          fill="divider"
                        />
                      </svg>
                    </Box>

                    <Box sx={{ display: "flex", alignItems: "center" }}>
                      <FormControlLabel
                        sx={{ marginLeft: "5px" }}
                        color="text.primary"
                        label="Add as new variable"
                        control={
                          <Checkbox
                            checked={false}
                            onChange={handleCheckboxChange}
                          />
                        }
                      />
                    </Box>
                  </Box>,
                );
              }
              return elements;
            })()}
          </Box>
        </Box>

        {/* action buttons */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "20px 16px 0 16px",
          }}
        >
          <Button
            fullWidth
            variant="outlined"
            onClick={() => {
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button
            fullWidth
            variant="contained"
            color="primary"
            onClick={() => {
              setConfirmationDialog(true);
            }}
          >
            Apply
          </Button>
        </Box>

        {/* confirmation dialog */}
        <PromptDialog
          open={confirmationDialoag}
          handleClose={() => setConfirmationDialog(false)}
          title="Applying these values will override the current values"
          content={
            <Typography>Do you want to proceed with this action?</Typography>
          }
          actionButtons={
            <Box
              display="flex"
              gap={3}
              alignItems="center"
              sx={{ flex: "1", justifyContent: "flex-end" }}
            >
              <Button
                variant="outlined"
                sx={{
                  minWidth: "150px",
                  borderRadius: "10px",
                }}
                onClick={() => {
                  setConfirmationDialog(false);
                }}
              >
                Cancel
              </Button>
              <Button
                sx={{
                  minWidth: "150px",
                  borderRadius: "10px",
                }}
                variant="contained"
                color="primary"
                onClick={handleSubmitVariable}
              >
                Apply Variables
              </Button>
            </Box>
          }
        />
      </Box>
    </Box>
  );
};

ImportDataset.propTypes = {
  onClose: PropTypes.func.isRequired,
  variables: PropTypes.array,
  setExtractedVars: PropTypes.any,
  setAppliedVariableData: PropTypes.func,
};

export default ImportDataset;
