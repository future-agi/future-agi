import {
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  InputAdornment,
  Skeleton,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import CustomTooltip from "src/components/tooltip";
import { useSimulationDetailContext } from "./context/SimulationDetailContext";
import VersionSelect from "./VersionSelect";
import SimulationEvaluationDrawer from "./SimulationEvaluationDrawer";
import SimulationExecutionsSelection from "./SimulationExecutionsSelection";
import {
  useSimulationEvaluationStoreShallow,
  useSimulationExecutionsGridStoreShallow,
} from "./states";
import { DRAWER_OPEN_ENUMS, getSelectedCount } from "./common";
import ScenarioPopover from "src/sections/test/TestRuns/ScenarioPopover";
import { useSelectedScenariosStore } from "src/sections/test/TestRuns/states";
import { AGENT_TYPES } from "../../../../agents/constants";
import SvgColor from "../../../../../components/svg-color";
import ModalWrapper from "../../../../../components/ModalWrapper/ModalWrapper";

const SimulationDetailHeader = ({ onBack }) => {
  const theme = useTheme();
  const { id: promptTemplateId } = useParams();
  const {
    simulation,
    isLoading,
    executionsCount,
    refreshGrid,
    searchQuery,
    setSearchQuery,
    getGridApi,
  } = useSimulationDetailContext();
  const [selectedVersion, setSelectedVersion] = useState("");
  const [scenarioPopoverOpen, setScenarioPopoverOpen] = useState(false);
  const { setOpenEvaluation, openEvaluation } =
    useSimulationEvaluationStoreShallow((s) => ({
      setOpenEvaluation: s.setOpenEvaluation,
      openEvaluation: s.openEvaluation,
    }));
  const scenarioButtonRef = useRef(null);
  const deleteExecutions = useMutation({
    mutationFn: () =>
      axios.post(endpoints.runTests.deleteSimulation(simulation?.id), {
        test_execution_ids: toggledNodes,
        select_all: selectAll,
      }),
    onSuccess: () => {
      enqueueSnackbar("Simulations deleted successfully", {
        variant: "success",
      });

      setOpenEvaluation(null);
      clearSelection();
      refreshGrid();
    },
  });
  // Use shared scenarios store
  const { selectedScenarios, setSelectedScenarios } =
    useSelectedScenariosStore();

  // Get selection state from store
  const {
    toggledNodes,
    selectAll,
    totalRowCount,
    setSelectAll,
    setToggledNodes,
  } = useSimulationExecutionsGridStoreShallow((s) => ({
    toggledNodes: s.toggledNodes,
    selectAll: s.selectAll,
    totalRowCount: s.totalRowCount,
    setSelectAll: s.setSelectAll,
    setToggledNodes: s.setToggledNodes,
  }));

  const gridApi = getGridApi?.();
  const selectedCount = useMemo(
    () => getSelectedCount(gridApi, toggledNodes, selectAll, totalRowCount),
    [gridApi, toggledNodes, selectAll, totalRowCount],
  );
  const clearSelection = useCallback(() => {
    const gridApi = getGridApi();
    gridApi?.deselectAll();
    setToggledNodes([]);
    setSelectAll(false);
  }, [getGridApi, setToggledNodes, setSelectAll]);

  const isInitializedRef = useRef(false);
  const isSyncingRef = useRef(false);
  const prevScenariosRef = useRef(null);
  const skipNextSyncRef = useRef(false);

  // Initialize selected values from simulation data (once only)
  if (simulation && !isInitializedRef.current) {
    skipNextSyncRef.current = true;
    setSelectedVersion(
      simulation.prompt_version || simulation.prompt_version_detail?.id || "",
    );
    setSelectedScenarios(simulation.scenarios || []);
    prevScenariosRef.current = simulation.scenarios || [];
    isInitializedRef.current = true;
  }

  // Update simulation mutation
  const { mutate: updateSimulation, isPending: isUpdating } = useMutation({
    /**
     *
     * @param {Object} data
     * @returns
     */
    mutationFn: async (data) => {
      return axios.patch(
        endpoints.promptSimulation.detail(promptTemplateId, simulation?.id),
        data,
      );
    },
    onSuccess: () => {
      enqueueSnackbar("Simulation updated successfully", {
        variant: "success",
      });
      isSyncingRef.current = false;
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.response?.data?.error || "Failed to update simulation",
        { variant: "error" },
      );
      isSyncingRef.current = false;
    },
  });

  // Sync scenario changes from store to simulation
  useEffect(() => {
    // Only sync if simulation is loaded and initialized
    if (!simulation || !isInitializedRef.current || !selectedScenarios) return;

    // Skip sync triggered by initialization to avoid stale store data race condition
    if (skipNextSyncRef.current) {
      skipNextSyncRef.current = false;
      return;
    }

    // Don't trigger update if scenarios match the simulation's current scenarios
    // This prevents false positives on initial load
    const simulationScenarios = simulation.scenarios || [];
    const isSameAsSimulation =
      selectedScenarios.length === simulationScenarios.length &&
      selectedScenarios.every((id) => simulationScenarios.includes(id));

    if (isSameAsSimulation) return;

    const prevScenarios = prevScenariosRef.current || [];
    const hasChanged =
      selectedScenarios.length !== prevScenarios.length ||
      selectedScenarios.some((id) => !prevScenarios.includes(id));

    if (hasChanged) {
      isSyncingRef.current = true;
      prevScenariosRef.current = selectedScenarios;
      updateSimulation({ scenario_ids: selectedScenarios });
    }
  }, [simulation, selectedScenarios, updateSimulation]);

  const handleVersionChange = (newVersion) => {
    if (newVersion === "create-new") {
      window.open(
        `/dashboard/workbench/create/${promptTemplateId}?tab=Playground`,
        "_blank",
      );
      return;
    }
    setSelectedVersion(newVersion);
    updateSimulation({ prompt_version_id: newVersion });
  };

  const { mutate: executeSimulation, isPending: isExecuting } = useMutation({
    mutationFn: async () => {
      return axios.post(
        endpoints.promptSimulation.execute(promptTemplateId, simulation?.id),
        {},
      );
    },
    onSuccess: () => {
      enqueueSnackbar("Simulation started successfully", {
        variant: "success",
      });
      refreshGrid();
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.response?.data?.error || "Failed to start simulation",
        { variant: "error" },
      );
    },
  });

  if (isLoading) {
    return (
      <Box display="flex" alignItems="center" gap={2} py={1}>
        <Skeleton variant="circular" width={32} height={32} />
        <Skeleton variant="text" width={200} height={28} />
      </Box>
    );
  }

  const evals =
    simulation?.simulate_eval_configs_detail || simulation?.evals_detail || [];

  const selectedScenarioIds = new Set(selectedScenarios || []);
  const hasEmptyScenario = (simulation?.scenarios_detail || []).some(
    (s) => selectedScenarioIds.has(s.id) && (s.dataset_rows || 0) === 0,
  );

  return (
    <>
      <Box
        display="flex"
        flexDirection="column"
        width="100%"
        gap={1}
        sx={{
          borderBottom: `1px solid ${theme.palette.divider}`,
          pb: 1.5,
        }}
      >
        {/* Main Row */}
        <Box
          display="flex"
          alignItems="center"
          gap={1.5}
          flexWrap="nowrap"
          minHeight={36}
        >
          {/* Left Side: Back, Name, Runs */}
          <IconButton
            onClick={onBack}
            size="small"
            sx={{
              border: `1px solid ${theme.palette.divider}`,
              borderRadius: 1,
            }}
          >
            <Iconify icon="eva:arrow-back-fill" width={18} height={18} />
          </IconButton>

          <Tooltip
            title={simulation?.name || "Simulation"}
            arrow
            placement="bottom"
          >
            <Typography
              variant="subtitle1"
              fontWeight={600}
              noWrap
              sx={{ maxWidth: 350 }}
            >
              {simulation?.name || "Simulation"}
            </Typography>
          </Tooltip>

          <Chip
            label={`${executionsCount} run${executionsCount !== 1 ? "s" : ""}`}
            size="small"
            variant="outlined"
            sx={{ height: 24, fontSize: "0.7rem" }}
          />

          {/* Spacer */}
          <Box flex={1} />

          {/* Right Side: Selection Options or Version, Scenarios, Evals, Run */}
          {selectedCount > 0 ? (
            <SimulationExecutionsSelection
              clearSelection={clearSelection}
              selectedCount={selectedCount}
            />
          ) : (
            <>
              {/* Version Dropdown */}
              <Box display="flex" alignItems="center" gap={0.5}>
                <Iconify
                  icon="mdi:source-branch"
                  width={16}
                  sx={{ color: "text.secondary" }}
                />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ whiteSpace: "nowrap" }}
                >
                  Version:
                </Typography>
                <VersionSelect
                  promptTemplateId={promptTemplateId}
                  value={selectedVersion}
                  onChange={handleVersionChange}
                  versionDetail={simulation?.prompt_version_detail}
                  disabled={isUpdating}
                />
              </Box>

              {/* Scenarios Button */}
              <Button
                ref={scenarioButtonRef}
                variant="outlined"
                size="small"
                onClick={() => setScenarioPopoverOpen(true)}
                startIcon={
                  <SvgColor
                    src="/assets/icons/navbar/ic_sessions.svg"
                    sx={{ width: "16px", height: "16px" }}
                  />
                }
              >
                Scenarios ({selectedScenarios.length})
              </Button>

              <ScenarioPopover
                open={scenarioPopoverOpen}
                onClose={() => setScenarioPopoverOpen(false)}
                anchor={scenarioButtonRef.current}
                simulationType={AGENT_TYPES.CHAT}
              />

              {/* Evaluations Button */}
              <Button
                variant="outlined"
                size="small"
                onClick={() => setOpenEvaluation(DRAWER_OPEN_ENUMS.EVALS)}
                startIcon={
                  <Iconify
                    sx={{
                      height: "16px",
                      width: "16px",
                    }}
                    icon="material-symbols:check-circle-outline"
                  />
                }
              >
                Evals ({evals.length})
              </Button>

              {/* Run Button */}
              <CustomTooltip
                show={hasEmptyScenario}
                title="Some selected scenarios have no datapoints. Remove them from the selection to run."
                size="small"
                arrow
              >
                <Box>
                  <Button
                    variant="contained"
                    size="small"
                    color="primary"
                    startIcon={
                      isExecuting ? (
                        <CircularProgress size={14} color="inherit" />
                      ) : (
                        <SvgColor src="/assets/icons/navbar/ic_get_started.svg" />
                      )
                    }
                    onClick={() => executeSimulation()}
                    disabled={
                      isExecuting ||
                      selectedScenarios.length === 0 ||
                      hasEmptyScenario
                    }
                  >
                    {isExecuting ? "Starting..." : "Run Simulation"}
                  </Button>
                </Box>
              </CustomTooltip>
            </>
          )}
        </Box>

        {/* Search Row */}
        <Box display="flex" alignItems="center">
          <TextField
            size="small"
            placeholder="Search runs..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            sx={{ width: 250 }}
            InputProps={{
              sx: { fontSize: "0.85rem" },
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="eva:search-fill"
                    width={16}
                    sx={{ color: "text.disabled" }}
                  />
                </InputAdornment>
              ),
              endAdornment: searchQuery && (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    onClick={() => setSearchQuery("")}
                    sx={{ p: 0.25 }}
                  >
                    <Iconify icon="eva:close-fill" width={14} />
                  </IconButton>
                </InputAdornment>
              ),
            }}
          />
        </Box>
      </Box>

      {/* Evaluation Drawer */}
      <SimulationEvaluationDrawer
        open={openEvaluation === DRAWER_OPEN_ENUMS.EVALS}
        onSuccess={() => {
          clearSelection();
          refreshGrid();
        }}
        onClose={() => setOpenEvaluation(null)}
      />

      <ModalWrapper
        open={openEvaluation === DRAWER_OPEN_ENUMS.DELETE}
        onClose={() => setOpenEvaluation(null)}
        title={`Delete Simulation${selectedCount !== 1 ? "s" : ""}`}
        subTitle="Deleting these runs will remove all simulation results and call details permanently. Are you sure you want to proceed?"
        isValid={true}
        modalWidth="480px"
        onSubmit={() => deleteExecutions.mutate()}
        onCancelBtn={() => setOpenEvaluation(null)}
        actionBtnTitle="Delete"
        actionBtnSx={{
          bgcolor: "red.500",
          color: "common.white",
          "&:hover": { bgcolor: "red.600" },
        }}
        isLoading={deleteExecutions.isPending}
      />
    </>
  );
};

SimulationDetailHeader.propTypes = {
  onBack: PropTypes.func.isRequired,
};

export default SimulationDetailHeader;
