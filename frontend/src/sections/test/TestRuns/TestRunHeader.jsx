import { Box, Button, InputAdornment, useTheme } from "@mui/material";
import React, { lazy, Suspense, useEffect, useRef, useState } from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { useMutation } from "@tanstack/react-query";
import { useParams } from "react-router";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import {
  useSelectedScenariosStore,
  // useSelectedSimulatorAgentsStore,
  useTestRunsSearchStore,
} from "./states";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "src/components/snackbar";
import { LoadingButton } from "@mui/lab";
import { useTestDetailContext } from "../context/TestDetailContext";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import { ShowComponent } from "src/components/show";
import { useTestRunsSelectedCount } from "../common";
import { useTestRunSdkStoreShallow } from "./state";
import { AGENT_TYPES } from "src/sections/agents/constants";
import { SIMULATION_TYPE } from "src/components/run-tests/common";
import { SCENARIO_STATUS } from "src/pages/dashboard/scenarios/common";

const ScenarioPopover = lazy(() => import("./ScenarioPopover"));
const TestRunsSelection = lazy(() => import("./TestRunsSelection"));
const NewVoiceSimulationDrawer = lazy(
  () => import("./NewVoiceSimulationDrawer"),
);

const TestRunHeader = () => {
  const theme = useTheme();
  const { role } = useAuthContext();
  const { search, setSearch } = useTestRunsSearchStore();
  const [scenarioPopoverOpen, setScenarioPopoverOpen] = useState(false);
  const scenarioPopoverRef = useRef(null);
  const { setSdkCodeOpen } = useTestRunSdkStoreShallow((state) => {
    return {
      setSdkCodeOpen: state.setSdkCodeOpen,
    };
  });
  // const { selectedSimulatorAgent } = useSelectedSimulatorAgentsStore();
  const { selectedScenarios, setSelectedScenarios } =
    useSelectedScenariosStore();
  const { testId } = useParams();
  const { testData } = useTestDetailContext();

  const sourceType = testData?.source_type ?? testData?.sourceType;
  const isPromptSimulation = sourceType === SIMULATION_TYPE.PROMPT;
  const promptTemplateId =
    testData?.prompt_template ?? testData?.promptTemplate;

  useEffect(() => {
    if (selectedScenarios.length === 0) {
      setSelectedScenarios(testData?.scenarios || []);
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [testData, setSelectedScenarios]);

  const { refreshTestRunGrid } = useTestDetailContext();

  const endpoint = isPromptSimulation
    ? endpoints.promptSimulation.execute(promptTemplateId, testId)
    : endpoints.runTests.runTest(testId);

  const { mutate: runTest, isPending: isRunningTest } = useMutation({
    mutationFn: () =>
      axios.post(
        endpoint,
        isPromptSimulation
          ? undefined
          : {
              select_all: false,
              scenario_ids: selectedScenarios,
            },
      ),
    onSuccess: () => {
      enqueueSnackbar("Test run started", { variant: "success" });
      refreshTestRunGrid();
    },
  });

  const selectedCount = useTestRunsSelectedCount();

  const isAgentDefinitionDeleted =
    !isPromptSimulation &&
    !(testData?.agent_definition ?? testData?.agentDefinition);

  const selectedScenarioIds = new Set(selectedScenarios || []);
  const scenarioDetails = testData?.scenarios_detail ?? [];
  const hasIncompleteScenario = scenarioDetails.some(
    (s) =>
      selectedScenarioIds.has(s.id) && s.status !== SCENARIO_STATUS.COMPLETED,
  );
  const agentType = isPromptSimulation
    ? AGENT_TYPES.CHAT
    : testData?.agent_version?.configuration_snapshot?.agent_type ??
      testData?.agentVersion?.configurationSnapshot?.agentType;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 1,
        minHeight: "40px",
      }}
    >
      <FormSearchField
        size="small"
        placeholder="Search"
        searchQuery={search}
        onChange={(e) => {
          setSearch(e.target.value);
        }}
        sx={{
          width: "279px",
          "& .MuiInputBase-input": {
            paddingY: `${theme.spacing(0.5)}`,
            paddingRight: `${theme.spacing(0.5)}`,
          },
        }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <SvgColor
                src={`/assets/icons/custom/search.svg`}
                sx={{ width: "20px", height: "20px", color: "text.disabled" }}
              />
            </InputAdornment>
          ),
          endAdornment: search && (
            <InputAdornment position="end">
              <Iconify
                icon="mingcute:close-line"
                onClick={() => {}}
                sx={{ color: "text.disabled", cursor: "pointer" }}
              />
            </InputAdornment>
          ),
        }}
        inputProps={{
          sx: {
            padding: 0,
          },
        }}
      />
      <ShowComponent condition={selectedCount > 0}>
        <Suspense fallback={null}>
          <TestRunsSelection />
        </Suspense>
      </ShowComponent>
      <ShowComponent condition={selectedCount === 0}>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button
            variant="outlined"
            size="small"
            onClick={() => {
              setScenarioPopoverOpen(true);
            }}
            ref={scenarioPopoverRef}
            sx={{ whiteSpace: "nowrap", minWidth: "fit-content" }}
            startIcon={
              <SvgColor
                src="/assets/icons/navbar/ic_sessions.svg"
                sx={{ width: "16px", height: "16px" }}
              />
            }
          >
            Scenarios ({selectedScenarios.length})
          </Button>
          <Suspense fallback={null}>
            <ScenarioPopover
              open={scenarioPopoverOpen}
              onClose={() => {
                setScenarioPopoverOpen(false);
              }}
              anchor={scenarioPopoverRef.current}
              simulationType={agentType}
            />
          </Suspense>
          <CustomTooltip
            show
            title="In beta, send early access request"
            size="small"
            arrow
          >
            <Box>
              <Button
                variant="outlined"
                size="small"
                onClick={() => {}}
                startIcon={
                  <SvgColor
                    src="/icons/datasets/calendar.svg"
                    sx={{ width: "16px", height: "16px" }}
                  />
                }
                disabled
              >
                Schedule
              </Button>
            </Box>
          </CustomTooltip>
          <CustomTooltip
            show
            title="In beta, send early access request"
            size="small"
            arrow
          >
            <Box>
              <Button
                variant="outlined"
                size="small"
                onClick={() => {}}
                startIcon={
                  <SvgColor
                    src="/assets/icons/app/ic_github_grey.svg"
                    sx={{ width: "16px", height: "16px" }}
                  />
                }
                disabled
                sx={{ whiteSpace: "nowrap", minWidth: "fit-content" }}
              >
                Github Actions
              </Button>
            </Box>
          </CustomTooltip>
          <CustomTooltip
            show={
              isAgentDefinitionDeleted ||
              selectedScenarios.length === 0 ||
              hasIncompleteScenario
            }
            title={
              isAgentDefinitionDeleted
                ? "Agent definition has been deleted. Please select a new agent definition to run simulation."
                : selectedScenarios.length === 0
                  ? "Select atleast one scenario to run test"
                  : "Some selected scenarios are not completed. Wait for them to finish or remove them from the selection."
            }
            size="small"
            arrow
          >
            <Box>
              <LoadingButton
                variant="contained"
                color="primary"
                size="small"
                startIcon={
                  <SvgColor src="/assets/icons/navbar/ic_get_started.svg" />
                }
                loading={isRunningTest}
                onClick={() => {
                  if (testId) {
                    trackEvent(Events.runTestRuntestClicked, {
                      [PropertyName.id]: testId,
                    });
                  }
                  // For CHAT agent type (non-prompt), show SDK code
                  if (agentType === AGENT_TYPES.CHAT && !isPromptSimulation) {
                    setSdkCodeOpen(true);
                    return;
                  }
                  runTest();
                }}
                sx={{ whiteSpace: "nowrap", minWidth: "fit-content" }}
                disabled={
                  !RolePermission.SIMULATION_AGENT[
                    PERMISSIONS.RUN_SIMULATION_TEST
                  ][role] ||
                  selectedScenarios.length === 0 ||
                  isAgentDefinitionDeleted ||
                  hasIncompleteScenario
                }
              >
                Run New Simulation
              </LoadingButton>
            </Box>
          </CustomTooltip>
        </Box>
      </ShowComponent>
      <Suspense fallback={null}>
        <NewVoiceSimulationDrawer />
      </Suspense>
    </Box>
  );
};

export default React.memo(TestRunHeader);
