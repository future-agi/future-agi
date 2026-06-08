import { Box, Button, InputAdornment, Stack, useTheme } from "@mui/material";
import React, {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { useMutation } from "@tanstack/react-query";
import { useLocation, useNavigate, useParams } from "react-router";
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
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import {
  buildVoiceRouteFocusPayload,
  getVoiceOnboardingParams,
  voiceSetupQuickStartAttributionFromSearch,
  VOICE_ONBOARDING_MODES,
} from "../onboardingVoiceRouteEvents";
import TestOnboardingFocusPanel from "../TestOnboardingFocusPanel";
import {
  buildEvalRunStepHref,
  buildEvalSourceFixRerunClickedPayload,
  buildEvalSourceFixRouteFocusPayload,
  EVAL_FIX_RERUN_ORIGINS,
  evalSetupQuickStartAttributionFromSearch,
  getEvalSourceFixOnboardingParams,
} from "src/sections/evals/components/evalCreateOnboarding";

const ScenarioPopover = lazy(() => import("./ScenarioPopover"));
const TestRunsSelection = lazy(() => import("./TestRunsSelection"));
const NewVoiceSimulationDrawer = lazy(
  () => import("./NewVoiceSimulationDrawer"),
);

const TestRunHeader = () => {
  const theme = useTheme();
  const { role } = useAuthContext();
  const location = useLocation();
  const navigate = useNavigate();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const recordedFocusRef = useRef(false);
  const recordedEvalSourceFixFocusRef = useRef(false);
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
    : testData?.agent_definition_detail?.agent_type ??
      testData?.agent_version?.configuration_snapshot?.agent_type ??
      testData?.agentVersion?.configurationSnapshot?.agentType;
  const voiceParams = useMemo(
    () => getVoiceOnboardingParams(location.search),
    [location.search],
  );
  const voiceQuickStartAttribution = useMemo(
    () => voiceSetupQuickStartAttributionFromSearch(location.search),
    [location.search],
  );
  const evalSourceFixParams = useMemo(
    () => getEvalSourceFixOnboardingParams(location.search),
    [location.search],
  );
  const evalQuickStartAttribution = useMemo(
    () => evalSetupQuickStartAttributionFromSearch(location.search),
    [location.search],
  );
  const showEvalSourceFixFocus = Boolean(
    evalSourceFixParams.isOnboarding &&
      evalSourceFixParams.sourceType === "simulation" &&
      evalSourceFixParams.sourceId === testId,
  );
  const evalSourceFixRerunHref = useMemo(() => {
    if (!showEvalSourceFixFocus || !evalSourceFixParams.evalId) return null;
    return buildEvalRunStepHref({
      evalId: evalSourceFixParams.evalId,
      previousRunId: evalSourceFixParams.runId,
      quickStartAttribution: evalQuickStartAttribution,
      rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
      setupLanguage: evalSourceFixParams.setupLanguage,
      setupProvider: evalSourceFixParams.setupProvider,
      sourceId: evalSourceFixParams.sourceId,
      sourceType: evalSourceFixParams.sourceType,
      traceId: evalSourceFixParams.traceId,
    });
  }, [evalQuickStartAttribution, evalSourceFixParams, showEvalSourceFixFocus]);
  const isRunTestCallMode =
    voiceParams.mode === VOICE_ONBOARDING_MODES.RUN_TEST_CALL;
  const isVoiceRunTestCallMode =
    isRunTestCallMode && agentType === AGENT_TYPES.VOICE;
  const runTestBlocker = (() => {
    if (isAgentDefinitionDeleted) return "Agent unavailable";
    if (selectedScenarios.length === 0) return "Select one scenario";
    if (hasIncompleteScenario) return "Scenario not ready";
    if (
      !RolePermission.SIMULATION_AGENT[PERMISSIONS.RUN_SIMULATION_TEST][role]
    ) {
      return "Permission required";
    }
    return null;
  })();

  const handleRunTestClick = () => {
    if (testId) {
      trackEvent(Events.runTestRuntestClicked, {
        [PropertyName.id]: testId,
      });
    }
    if (agentType === AGENT_TYPES.CHAT && !isPromptSimulation) {
      setSdkCodeOpen(true);
      return;
    }
    runTest();
  };

  useEffect(() => {
    if (!isRunTestCallMode || agentType !== AGENT_TYPES.VOICE || !testId) {
      return;
    }
    if (recordedFocusRef.current) return;
    recordedFocusRef.current = true;
    recordActivationEvent?.(
      buildVoiceRouteFocusPayload({
        mode: voiceParams.mode,
        quickStartAttribution: voiceQuickStartAttribution,
        source: "voice_simulation_runs",
        testId,
        agentDefinitionId: voiceParams.agentDefinitionId,
      }),
    );
  }, [
    agentType,
    isRunTestCallMode,
    recordActivationEvent,
    testId,
    voiceQuickStartAttribution,
    voiceParams.agentDefinitionId,
    voiceParams.mode,
  ]);

  useEffect(() => {
    if (!showEvalSourceFixFocus || recordedEvalSourceFixFocusRef.current)
      return;
    recordedEvalSourceFixFocusRef.current = true;
    recordActivationEvent?.(
      buildEvalSourceFixRouteFocusPayload({
        evalId: evalSourceFixParams.evalId,
        quickStartAttribution: evalQuickStartAttribution,
        route: "simulation_runs",
        runId: evalSourceFixParams.runId,
        setupLanguage: evalSourceFixParams.setupLanguage,
        setupProvider: evalSourceFixParams.setupProvider,
        sourceId: evalSourceFixParams.sourceId,
        sourceType: evalSourceFixParams.sourceType,
        traceId: evalSourceFixParams.traceId,
      }),
    );
  }, [
    evalQuickStartAttribution,
    evalSourceFixParams,
    recordActivationEvent,
    showEvalSourceFixFocus,
  ]);

  const handleEvalSourceFixRerun = () => {
    if (!evalSourceFixRerunHref) return;
    const navigateToRerun = () => navigate(evalSourceFixRerunHref);
    if (recordActivationEvent) {
      recordActivationEvent(
        buildEvalSourceFixRerunClickedPayload({
          evalId: evalSourceFixParams.evalId,
          quickStartAttribution: evalQuickStartAttribution,
          rerunRoute: evalSourceFixRerunHref,
          route: "simulation_runs",
          runId: evalSourceFixParams.runId,
          setupLanguage: evalSourceFixParams.setupLanguage,
          setupProvider: evalSourceFixParams.setupProvider,
          sourceId: evalSourceFixParams.sourceId,
          sourceType: evalSourceFixParams.sourceType,
          traceId: evalSourceFixParams.traceId,
        }),
        { onSettled: navigateToRerun },
      );
    } else {
      navigateToRerun();
    }
  };

  return (
    <Stack spacing={1.5} sx={{ width: "100%" }}>
      <TestOnboardingFocusPanel
        currentStep="Fix source"
        description="Update the simulation scenario or expected behavior that produced the weak result, then rerun the quality check."
        eyebrow="Simulation / Evals"
        hidden={!showEvalSourceFixFocus}
        primaryAction={{
          label: "Rerun quality check",
          onClick: handleEvalSourceFixRerun,
          disabled: !evalSourceFixRerunHref,
        }}
        steps={[
          { label: "Run", complete: true },
          { label: "Review", complete: true },
          { label: "Fix source", complete: false },
          { label: "Rerun", complete: false },
        ]}
        title="Fix the simulation source"
        tourAnchor={evalSourceFixParams.tourAnchor}
        sx={{ mb: 0 }}
      />
      <TestOnboardingFocusPanel
        currentStep="Test call"
        description="Run one test call with the selected voice agent. When the call finishes, we open the call review and then guide you to success criteria."
        eyebrow="Voice setup"
        hidden={!isVoiceRunTestCallMode}
        blocker={runTestBlocker}
        primaryAction={{
          label: isRunningTest ? "Running test call" : "Run test call",
          onClick: handleRunTestClick,
          disabled: Boolean(runTestBlocker) || isRunningTest,
        }}
        singleActionFocus={isVoiceRunTestCallMode}
        steps={[
          { label: "Agent", complete: Boolean(voiceParams.agentDefinitionId) },
          { label: "Test call", complete: false },
          { label: "Review call", complete: false },
          { label: "Success criteria", complete: false },
        ]}
        title="Run a voice test call"
        tourAnchor={voiceParams.tourAnchor}
        sx={{ mb: 0 }}
      />
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
                  sx={{
                    width: "20px",
                    height: "20px",
                    color: "text.disabled",
                  }}
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
        <ShowComponent condition={selectedCount > 0 && !isVoiceRunTestCallMode}>
          <Suspense fallback={null}>
            <TestRunsSelection />
          </Suspense>
        </ShowComponent>
        <ShowComponent
          condition={selectedCount === 0 && !isVoiceRunTestCallMode}
        >
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
                  GitHub Actions
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
                    ? "Select at least one scenario to run the simulation."
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
                  onClick={handleRunTestClick}
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
    </Stack>
  );
};

export default React.memo(TestRunHeader);
