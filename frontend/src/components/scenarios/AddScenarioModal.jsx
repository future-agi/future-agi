import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Box,
  Typography,
  Button,
  Tab,
  RadioGroup,
  FormControlLabel,
  Radio,
  IconButton,
  CircularProgress,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useDebounce } from "src/hooks/use-debounce";
import { useForm } from "react-hook-form";
import FormTextFieldV2 from "../FormTextField/FormTextFieldV2";
import { enqueueSnackbar } from "notistack";
import CustomTooltip from "../tooltip";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { CustomTabs } from "src/components/tabs/tabs";
import { ShowComponent } from "src/components/show";
import ScenarioGraphBuilder from "./ScenarioGraphBuilder";

const TabWithTooltip = ({ label, disabled, tooltip, ...tabProps }) => {
  if (disabled && tooltip) {
    return (
      <CustomTooltip show={true} title={tooltip}>
        <span>
          <Tab label={label} disabled={disabled} {...tabProps} />
        </span>
      </CustomTooltip>
    );
  }
  return <Tab label={label} disabled={disabled} {...tabProps} />;
};

TabWithTooltip.propTypes = {
  label: PropTypes.string,
  disabled: PropTypes.bool,
  tooltip: PropTypes.string,
};

const AddScenarioModal = ({ open, onClose, onCreateSuccess }) => {
  const [activeTab, setActiveTab] = useState(0);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const theme = useTheme();

  // Form control setup
  const { control, handleSubmit, reset, watch } = useForm({
    defaultValues: {
      scenarioName: "",
      scenarioDescription: "",
    },
  });

  const scenarioName = watch("scenarioName");

  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

  // Fetch datasets
  const { data: datasetsData, isLoading: isDatasetsLoading } = useQuery({
    queryKey: ["datasets", debouncedSearchQuery],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.develop.getDatasets(), {
        params: {
          search_text: debouncedSearchQuery?.length
            ? debouncedSearchQuery
            : null,
          page: 0,
          page_size: 100, // Get more datasets for selection
        },
      });
      return data;
    },
    enabled: open,
  });

  const datasets = datasetsData?.result?.datasets || [];

  const handleClose = () => {
    setSelectedDataset("");
    setSearchQuery("");
    setActiveTab(0);
    reset(); // Reset form fields
    onClose();
  };

  const handleCreateScenario = async (formData) => {
    if (!selectedDataset || !formData.scenarioName?.trim()) return;

    setIsCreating(true);
    try {
      const payload = {
        name: formData.scenarioName.trim(),
        description: formData.scenarioDescription?.trim() || "",
        dataset_id: selectedDataset,
        kind: "dataset",
      };

      trackEvent(Events.scenarioCreateClicked, {
        [PropertyName.formFields]: payload,
      });

      await axios.post(endpoints.scenarios.create, payload);
      enqueueSnackbar("Scenario created successfully", { variant: "warning" });
      onCreateSuccess?.();
      handleClose();
    } catch (error) {
      enqueueSnackbar("Error creating scenerio", { variant: "error" });
      // You might want to show an error message to the user here
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          width: "500px",
          maxHeight: "800px",
        },
      }}
    >
      <DialogTitle>
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Box>
            <Typography variant="h6" fontWeight="fontWeightSemiBold">
              Create New Scenario
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Create a new test scenario by uploading a dataset, script, or
              building a graph
            </Typography>
          </Box>
          <IconButton
            onClick={handleClose}
            size="small"
            sx={{ position: "absolute", top: "12px", right: "12px" }}
          >
            <Iconify
              icon="eva:close-fill"
              color="text.primary"
              height={24}
              width={24}
            />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent
        sx={{
          p: 3,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        {/* Scenario Details Section - Outside of tabs */}
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            paddingY: 0.5,
          }}
        >
          <FormTextFieldV2
            label="Scenario Name"
            control={control}
            fieldName="scenarioName"
            size="small"
            required
            fullWidth
            placeholder="Enter scenario name"
          />
          <FormTextFieldV2
            label="Description"
            control={control}
            fieldName="scenarioDescription"
            size="small"
            fullWidth
            multiline
            placeholder="Enter scenario description"
            rows={3}
          />
        </Box>
        {/* Tabs Section */}
        <Box sx={{ mx: -3, borderBottom: 1, borderColor: "divider" }}>
          <Box sx={{ px: 3 }}>
            <CustomTabs
              value={activeTab}
              onChange={(e, newValue) => setActiveTab(newValue)}
            >
              <Tab label="Import Dataset" />
              <Tab label="Build Graph" />
              <CustomTooltip
                show={true}
                placement="top"
                arrow
                title="Switch to enterprise plan to access this feature"
              >
                <Tab label="Upload Script" disabled />
              </CustomTooltip>
            </CustomTabs>
          </Box>
        </Box>

        {/* Tab Content */}
        <Box
          sx={{
            flex: 1,
            overflowY: "auto",
            maxHeight: "300px", // adjust this height if needed
          }}
        >
          {activeTab === 0 && (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <Box>
                    <Typography
                      variant="subtitle1"
                      fontWeight="fontWeightMedium"
                    >
                      Select Datasets
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Choose a dataset to create your scenario
                    </Typography>
                  </Box>
                </Box>

                <FormSearchField
                  size="small"
                  placeholder="Search datasets..."
                  searchQuery={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  InputProps={{}}
                />
              </Box>

              <Box
                sx={{
                  maxHeight: "300px",
                  overflowY: "auto",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  p: 1,
                }}
              >
                {isDatasetsLoading ? (
                  <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
                    <CircularProgress size={24} />
                  </Box>
                ) : datasets.length === 0 ? (
                  <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
                    <Typography variant="body2" color="text.secondary">
                      {searchQuery
                        ? "No datasets found"
                        : "No datasets available"}
                    </Typography>
                  </Box>
                ) : (
                  <RadioGroup
                    value={selectedDataset}
                    onChange={(e) => setSelectedDataset(e.target.value)}
                  >
                    <Table size="small">
                      <TableHead>
                        <TableRow
                          sx={{
                            bgcolor: "background.neutral",
                          }}
                        >
                          <TableCell
                            sx={{
                              width: 60,
                              p: 1,
                              bgcolor: "background.neutral",
                              borderRight: "2px solid",
                              borderRightColor: `${theme.palette.divider} !important`,
                            }}
                          ></TableCell>
                          <TableCell
                            sx={{
                              p: 1,
                              fontWeight: "fontWeightMedium",
                              bgcolor: "background.neutral",
                              color: "text.secondary",
                              borderRight: "2px solid",
                              borderRightColor: `${theme.palette.divider} !important`,
                            }}
                          >
                            Dataset Name
                          </TableCell>
                          <TableCell
                            sx={{
                              width: 100,
                              p: 1,
                              fontWeight: "fontWeightMedium",
                              bgcolor: "background.neutral",
                              color: "text.secondary",
                            }}
                            align="left"
                          >
                            Records
                          </TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {datasets.map((dataset) => (
                          <TableRow
                            key={dataset.id}
                            hover
                            sx={{
                              cursor: "pointer",
                              "&:hover": {
                                backgroundColor: "background.default",
                              },
                            }}
                            onClick={() =>
                              setSelectedDataset(dataset.id.toString())
                            }
                          >
                            <TableCell sx={{ p: 1 }}>
                              <FormControlLabel
                                value={dataset.id.toString()}
                                control={<Radio size="small" />}
                                label=""
                                sx={{ m: 0 }}
                              />
                            </TableCell>
                            <TableCell sx={{ p: 1 }}>
                              <Box
                                sx={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: 1.5,
                                }}
                              >
                                <Box sx={{ minWidth: 0 }}>
                                  <Typography
                                    variant="body2"
                                    fontWeight="fontWeightMedium"
                                    noWrap
                                  >
                                    {dataset.name}
                                  </Typography>
                                </Box>
                              </Box>
                            </TableCell>
                            <TableCell sx={{ p: 1 }} align="left">
                              <Typography
                                variant="body2"
                                color="text.secondary"
                              >
                                {dataset.number_of_datapoints ??
                                  dataset.numberOfDatapoints ??
                                  0}
                              </Typography>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </RadioGroup>
                )}
              </Box>
            </Box>
          )}
          <ShowComponent condition={activeTab === 1}>
            <ScenarioGraphBuilder />
          </ShowComponent>
        </Box>
      </DialogContent>

      <DialogActions sx={{ p: 3, gap: 1 }}>
        <Button onClick={handleClose} color="inherit" variant="outlined">
          Cancel
        </Button>
        <Button
          onClick={handleSubmit(handleCreateScenario)}
          variant="contained"
          color="primary"
          disabled={!selectedDataset || !scenarioName?.trim() || isCreating}
          startIcon={isCreating ? <CircularProgress size={16} /> : null}
        >
          {isCreating ? "Creating..." : "Create Scenario"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

AddScenarioModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onCreateSuccess: PropTypes.func,
};

export default AddScenarioModal;
