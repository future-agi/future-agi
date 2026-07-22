import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  InputLabel,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Select,
  Skeleton,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { getVersionLabel } from "src/utils/utils";
import { format } from "date-fns";
import { useGetScenarioList } from "src/api/scenarios/scenarios";
import CustomTooltip from "src/components/tooltip";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { usePromptVersions } from "../hooks/use-prompt-versions";

// Generate auto name based on date/time
const generateAutoName = () => {
  const now = new Date();
  const dateStr = format(now, "MMM d");
  const timeStr = format(now, "h:mm a");
  return `Simulation - ${dateStr} at ${timeStr}`;
};

const CreateSimulationModal = ({ open, onClose, onSuccess }) => {
  const theme = useTheme();
  const { id: promptTemplateId } = useParams();
  const queryClient = useQueryClient();

  const [formData, setFormData] = useState({
    name: "",
    description: "",
    scenarioIds: [],
    versionId: "",
  });
  const [isStarting, setIsStarting] = useState(false);

  // Fetch available scenarios using existing hook
  const {
    data: scenariosData,
    isLoading: isLoadingScenarios,
    refetch: refetchScenarios,
    isFetching: isFetchingScenarios,
  } = useGetScenarioList(undefined, {
    simulationType: AGENT_TYPES.CHAT,
  });

  // Flatten scenarios from infinite query pages
  const scenarios = useMemo(
    () =>
      scenariosData?.pages?.reduce(
        (acc, page) => [...acc, ...(page.data?.results || [])],
        [],
      ) || [],
    [scenariosData],
  );

  // Fetch prompt versions using existing hook
  const {
    versions,
    isLoading: isLoadingVersions,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = usePromptVersions(open ? promptTemplateId : null);

  // Load the next page when the version dropdown is scrolled near the bottom.
  const handleVersionMenuScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    if (
      scrollHeight - scrollTop - clientHeight < 50 &&
      hasNextPage &&
      !isFetchingNextPage
    ) {
      fetchNextPage();
    }
  };

  // Reset form when modal opens with auto-generated name
  useEffect(() => {
    if (open) {
      setFormData({
        name: generateAutoName(),
        description: "",
        scenarioIds: [],
        versionId: "",
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Update version when versions load
  useEffect(() => {
    if (versions.length > 0 && !formData.versionId) {
      const defaultVersion = versions.find((v) => v.is_default) || versions[0];
      setFormData((prev) => ({
        ...prev,
        versionId: defaultVersion?.id || "",
      }));
    }
  }, [versions, formData.versionId]);

  const selectableScenarios = useMemo(
    () => scenarios.filter((s) => (s.dataset_rows || 0) > 0),
    [scenarios],
  );

  const handleScenarioToggle = (scenarioId) => {
    setFormData((prev) => {
      const isSelected = prev.scenarioIds.includes(scenarioId);
      if (!isSelected) {
        const scenario = scenarios.find((s) => s.id === scenarioId);
        if (scenario && (scenario.dataset_rows || 0) === 0) return prev;
      }
      const newScenarioIds = isSelected
        ? prev.scenarioIds.filter((id) => id !== scenarioId)
        : [...prev.scenarioIds, scenarioId];
      return { ...prev, scenarioIds: newScenarioIds };
    });
  };

  const handleSelectAll = () => {
    if (formData.scenarioIds.length === selectableScenarios.length) {
      setFormData((prev) => ({ ...prev, scenarioIds: [] }));
    } else {
      setFormData((prev) => ({
        ...prev,
        scenarioIds: selectableScenarios.map((s) => s.id),
      }));
    }
  };

  const handleRefreshScenarios = () => {
    refetchScenarios();
  };

  const handleCreateNewScenario = () => {
    // Open scenarios page in new tab
    window.open("/dashboard/simulate/scenarios/create", "_blank");
  };

  const { mutate: createSimulation, isPending: isCreating } = useMutation({
    mutationFn: async (data) => {
      if (!data.versionId) {
        throw new Error("No prompt version selected");
      }

      return axios.post(
        endpoints.promptSimulation.simulations(promptTemplateId),
        {
          name: data.name,
          description: data.description,
          prompt_version_id: data.versionId,
          scenario_ids: data.scenarioIds,
        },
      );
    },
    onSuccess: async (response) => {
      const simulationId = response?.data?.result?.id;
      // Invalidate the simulations list cache to trigger a refresh
      queryClient.invalidateQueries({
        queryKey: ["run-tests", "prompt", promptTemplateId],
      });
      if (simulationId) {
        setIsStarting(true);
        try {
          await axios.post(
            endpoints.promptSimulation.execute(promptTemplateId, simulationId),
            {},
          );
          enqueueSnackbar("Simulation created and execution started", {
            variant: "success",
          });
        } catch (error) {
          enqueueSnackbar(
            error?.response?.data?.error ||
              "Simulation created, but failed to start execution",
            { variant: "warning" },
          );
        } finally {
          setIsStarting(false);
        }
      } else {
        enqueueSnackbar("Simulation created successfully", {
          variant: "success",
        });
      }
      onSuccess?.(simulationId);
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.response?.data?.error || "Failed to create simulation",
        { variant: "error" },
      );
    },
  });

  const handleSubmit = () => {
    if (!formData.name.trim()) {
      enqueueSnackbar("Please enter a simulation name", { variant: "warning" });
      return;
    }
    if (!formData.versionId) {
      enqueueSnackbar("Please select a prompt version", { variant: "warning" });
      return;
    }
    if (formData.scenarioIds.length === 0) {
      enqueueSnackbar("Please select at least one scenario", {
        variant: "warning",
      });
      return;
    }
    const scenarioIds = formData.scenarioIds.filter((id) => {
      const scenario = scenarios.find((s) => s.id === id);
      return !scenario || (scenario.dataset_rows || 0) > 0;
    });
    createSimulation({ ...formData, scenarioIds });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: { borderRadius: 2 },
      }}
    >
      <DialogTitle>
        <Box display="flex" justifyContent="space-between" alignItems="center">
          <Typography variant="h6">Create Chat Simulation</Typography>
          <IconButton onClick={onClose} size="small">
            <Iconify icon="eva:close-fill" />
          </IconButton>
        </Box>
      </DialogTitle>

      <DialogContent dividers>
        <Box display="flex" flexDirection="column" gap={2.5} py={1}>
          {/* Name field */}
          <TextField
            label="Simulation Name"
            placeholder="Enter a name for this simulation"
            value={formData.name}
            onChange={(e) =>
              setFormData((prev) => ({ ...prev, name: e.target.value }))
            }
            fullWidth
            required
            size="small"
          />

          {/* Version dropdown */}
          <FormControl fullWidth size="small">
            <InputLabel>Prompt Version</InputLabel>
            <Select
              value={formData.versionId}
              label="Prompt Version"
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, versionId: e.target.value }))
              }
              disabled={isLoadingVersions}
              MenuProps={{
                PaperProps: {
                  sx: { maxHeight: 300 },
                  onScroll: handleVersionMenuScroll,
                },
              }}
            >
              {versions.map((version) => (
                <MenuItem key={version.id} value={version.id}>
                  <Box display="flex" alignItems="center" gap={1}>
                    <span>{getVersionLabel(version.template_version)}</span>
                    {version.is_default && (
                      <Typography
                        component="span"
                        variant="caption"
                        sx={{
                          backgroundColor: theme.palette.primary.main,
                          color: "primary.contrastText",
                          px: 0.75,
                          py: 0.25,
                          borderRadius: 0.5,
                          fontSize: "0.65rem",
                        }}
                      >
                        Default
                      </Typography>
                    )}
                    {version.is_draft && (
                      <Typography
                        component="span"
                        variant="caption"
                        sx={{
                          backgroundColor: theme.palette.warning.main,
                          color: "warning.contrastText",
                          px: 0.75,
                          py: 0.25,
                          borderRadius: 0.5,
                          fontSize: "0.65rem",
                        }}
                      >
                        Draft
                      </Typography>
                    )}
                  </Box>
                </MenuItem>
              ))}
              {isFetchingNextPage &&
                Array.from({ length: 2 }).map((_, index) => (
                  <MenuItem key={`version-skeleton-${index}`} disabled>
                    <Skeleton variant="text" width={60} />
                  </MenuItem>
                ))}
            </Select>
          </FormControl>

          {/* Description field */}
          <TextField
            label="Description (optional)"
            placeholder="Describe the purpose of this simulation"
            value={formData.description}
            onChange={(e) =>
              setFormData((prev) => ({ ...prev, description: e.target.value }))
            }
            fullWidth
            multiline
            rows={2}
            size="small"
          />

          {/* Scenarios selection */}
          <Box>
            <Box
              display="flex"
              justifyContent="space-between"
              alignItems="center"
              mb={1}
            >
              <Typography variant="subtitle2">Select Scenarios</Typography>
              <Box display="flex" gap={0.5}>
                <Tooltip title="Refresh scenarios">
                  <IconButton
                    size="small"
                    onClick={handleRefreshScenarios}
                    disabled={isFetchingScenarios}
                  >
                    <Iconify
                      icon="mdi:refresh"
                      width={18}
                      sx={{
                        animation: isFetchingScenarios
                          ? "spin 1s linear infinite"
                          : "none",
                        "@keyframes spin": {
                          "0%": { transform: "rotate(0deg)" },
                          "100%": { transform: "rotate(360deg)" },
                        },
                      }}
                    />
                  </IconButton>
                </Tooltip>
                {selectableScenarios.length > 0 && (
                  <Button size="small" onClick={handleSelectAll}>
                    {formData.scenarioIds.length === selectableScenarios.length
                      ? "Deselect All"
                      : "Select All"}
                  </Button>
                )}
              </Box>
            </Box>

            {isLoadingScenarios ? (
              <Box display="flex" justifyContent="center" py={3}>
                <CircularProgress size={24} />
              </Box>
            ) : (
              <List
                sx={{
                  maxHeight: 200,
                  overflow: "auto",
                  border: `1px solid ${theme.palette.divider}`,
                  borderRadius: 1,
                }}
              >
                {/* Create New Scenario option */}
                <ListItem disablePadding>
                  <ListItemButton
                    onClick={handleCreateNewScenario}
                    sx={{
                      borderBottom: `1px solid ${theme.palette.divider}`,
                      backgroundColor: theme.palette.background.default,
                    }}
                  >
                    <ListItemIcon sx={{ minWidth: 36 }}>
                      <Iconify
                        icon="mdi:plus-circle-outline"
                        width={20}
                        sx={{ color: theme.palette.primary.main }}
                      />
                    </ListItemIcon>
                    <ListItemText
                      primary="Create New Chat Scenario"
                      primaryTypographyProps={{
                        variant: "body2",
                        color: "primary",
                        fontWeight: 500,
                      }}
                    />
                    <Iconify
                      icon="mdi:open-in-new"
                      width={16}
                      sx={{ color: theme.palette.text.secondary }}
                    />
                  </ListItemButton>
                </ListItem>

                {scenarios.length === 0 ? (
                  <ListItem>
                    <ListItemText
                      primary="No scenarios available"
                      secondary="Create a new scenario to get started"
                      primaryTypographyProps={{
                        variant: "body2",
                        color: "text.secondary",
                        textAlign: "center",
                      }}
                      secondaryTypographyProps={{
                        variant: "caption",
                        textAlign: "center",
                      }}
                    />
                  </ListItem>
                ) : (
                  scenarios.map((scenario) => {
                    const isEmpty = (scenario.dataset_rows || 0) === 0;
                    return (
                      <CustomTooltip
                        key={scenario.id}
                        show={isEmpty}
                        title="This scenario has no datapoints to run against."
                        placement="left"
                        arrow
                        size="small"
                      >
                        <ListItem disablePadding>
                          <ListItemButton
                            onClick={() => handleScenarioToggle(scenario.id)}
                            disabled={isEmpty}
                            dense
                          >
                            <ListItemIcon sx={{ minWidth: 36 }}>
                              <Checkbox
                                edge="start"
                                checked={formData.scenarioIds.includes(
                                  scenario.id,
                                )}
                                disabled={isEmpty}
                                tabIndex={-1}
                                disableRipple
                                size="small"
                              />
                            </ListItemIcon>
                            <ListItemText
                              primary={scenario.name}
                              secondary={
                                isEmpty
                                  ? "No datapoints"
                                  : scenario.description ||
                                    scenario.scenarioType
                              }
                              primaryTypographyProps={{ variant: "body2" }}
                              secondaryTypographyProps={{ variant: "caption" }}
                            />
                          </ListItemButton>
                        </ListItem>
                      </CustomTooltip>
                    );
                  })
                )}
              </List>
            )}
            {formData.scenarioIds.length > 0 && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mt: 0.5, display: "block" }}
              >
                {formData.scenarioIds.length} scenario(s) selected
              </Typography>
            )}
          </Box>
        </Box>
      </DialogContent>

      <DialogActions sx={{ p: 2 }}>
        <Button onClick={onClose} color="inherit">
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={
            isCreating ||
            isStarting ||
            !formData.name.trim() ||
            !formData.versionId ||
            formData.scenarioIds.length === 0
          }
          startIcon={
            (isCreating || isStarting) && (
              <CircularProgress size={16} color="inherit" />
            )
          }
          sx={{
            backgroundColor: "primary.main",
            "&:hover": {
              backgroundColor: "primary.main",
            },
          }}
        >
          {isCreating
            ? "Creating..."
            : isStarting
              ? "Starting..."
              : "Create Simulation"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CreateSimulationModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSuccess: PropTypes.func,
};

export default CreateSimulationModal;
