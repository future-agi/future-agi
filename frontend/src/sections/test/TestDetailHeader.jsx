import {
  Box,
  Button,
  CircularProgress,
  MenuItem,
  Popover,
  Skeleton,
  styled,
  Typography,
  useTheme,
} from "@mui/material";
import React, {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { getVersionLabel } from "src/utils/utils";
import { resetState, useTestEvaluationStoreShallow } from "./states";
import { useIsMutating } from "@tanstack/react-query";
import { useTestRunsList } from "src/api/tests/testRuns";
import { useScrollEnd } from "../../hooks/use-scroll-end";
import { useDebounce } from "src/hooks/use-debounce";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import {
  resetState as resetTestRunsState,
  useSelectedAgentDefinitionStore,
  useSelectedSimulatorAgentsStore,
} from "./TestRuns/states";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";
import { useTestDetailContext } from "./context/TestDetailContext";
import { SIMULATION_TYPE } from "src/components/run-tests/common";

const AgentDefinitionPopover = lazy(
  () => import("./TestRuns/AgentDefinitionPopover"),
);
const AgentDefinitionVersionPopover = lazy(
  () => import("./TestRuns/AgenetDefinitionVersionPopover"),
);

const CustomBackButton = styled(Button)(({ theme }) => ({
  borderWidth: "1px",
  borderColor: theme.palette.action.hover,
  borderRadius: "4px",
}));

const TestsDropdownButton = styled(Button)(({ theme }) => ({
  minWidth: 227,
  height: 40,
  justifyContent: "space-between",
  textTransform: "none",
  border: "1px solid",
  borderColor: theme.palette.divider,
  borderRadius: theme.spacing(0.5),
  backgroundColor: theme.palette.background.paper,
  color: theme.palette.text.primary,
  padding: theme.spacing(0.375, 1.5),
  "&:hover": {
    backgroundColor: theme.palette.background.default,
    borderColor: theme.palette.text.disabled,
  },
}));

const SavingStatus = {
  SAVING: "SAVING",
  SAVED: "SAVED",
  IDLE: "IDLE",
};

const TestDetailHeader = () => {
  const theme = useTheme();
  const { role } = useAuthContext();
  const [testSelectDropDownOpen, setTestSelectDropDownOpen] = useState(false);
  const testSelectDropDownRef = useRef(null);
  // const [simulatorPopoverOpen, setSimulatorPopoverOpen] = useState(false);
  // const simulatorPopoverRef = useRef(null);
  const { selectedSimulatorAgent, setSelectedSimulatorAgent } =
    useSelectedSimulatorAgentsStore();
  const {
    selectedAgentDefinition,
    setSelectedAgentDefinition,
    selectedAgentDefinitionVersion,
    setSelectedAgentDefinitionVersion,
  } = useSelectedAgentDefinitionStore();
  const [agentDefinitionPopoverOpen, setAgentDefinitionPopoverOpen] =
    useState(false);
  const [
    agentDefinitionVersionPopoverOpen,
    setAgentDefinitionVersionPopoverOpen,
  ] = useState(false);
  const agentDefinitionPopoverRef = useRef(null);
  const agentDefinitionVersionPopoverRef = useRef(null);

  const { testId } = useParams();
  const [savingStatus, setSavingStatus] = useState(SavingStatus.IDLE);
  const savingTimeoutRef = useRef(null);
  const firstRenderRef = useRef(false);

  const [searchText, setSearchText] = useState();
  const debouncedSearchText = useDebounce(searchText, 300);

  const setOpenTestEvaluation = useTestEvaluationStoreShallow(
    (s) => s.setOpenTestEvaluation,
  );

  const navigate = useNavigate();

  const { testData, isTestDataPending: isPendingTestDetail } =
    useTestDetailContext();
  const agentVersion = testData?.agent_version ?? testData?.agentVersion;
  const configSnapshot =
    agentVersion?.configuration_snapshot ?? agentVersion?.configurationSnapshot;
  const agentType = configSnapshot?.agent_type ?? configSnapshot?.agentType;
  const sourceType = testData?.source_type ?? testData?.sourceType;
  const isPromptSimulation = sourceType === SIMULATION_TYPE.PROMPT;
  useEffect(() => {
    if (!selectedSimulatorAgent) {
      setSelectedSimulatorAgent(
        testData?.simulator_agent_detail ?? testData?.simulatorAgentDetail,
      );
    }
    setSelectedAgentDefinition({
      ...configSnapshot,
      id: testData?.agent_definition ?? testData?.agentDefinition,
    });
    setSelectedAgentDefinitionVersion({
      value: agentVersion?.id,
      label: agentVersion?.name,
    });

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [testData, setSelectedSimulatorAgent]);

  const {
    testsList,
    fetchNextPage,
    isFetchingNextPage: isFetchingTestListNextPage,
    isFetching: isFetchingTestList,
  } = useTestRunsList({
    searchText: debouncedSearchText,
    enabled: testSelectDropDownOpen,
  });

  const scrollContainer = useScrollEnd(() => {
    if (isFetchingTestList || isFetchingTestListNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingTestListNextPage, isFetchingTestList]);

  const isLoadingTests = isPendingTestDetail;

  const handleTestSelect = useCallback(
    (selectedTestId) => {
      const lastParam = window.location.pathname
        .split("/")
        .filter((v) => v.length)
        .pop();

      resetTestRunsState();
      resetState();
      navigate(`/dashboard/simulate/test/${selectedTestId}/${lastParam}/`);
    },
    [navigate],
  );

  const handleBack = useCallback(() => {
    navigate(`/dashboard/simulate/test/`);
  }, [navigate]);

  const handleEvalClick = useCallback(() => {
    if (!testId) return;
    trackEvent(Events.runTestEvalClicked, {
      [PropertyName.id]: testId,
    });
    setOpenTestEvaluation(true);
  }, [testId, setOpenTestEvaluation]);

  const updateCount = useIsMutating({
    mutationKey: ["update-test-runs", testId],
  });

  useEffect(() => {
    if (!firstRenderRef.current) {
      firstRenderRef.current = true;
      return;
    }

    if (updateCount > 0) {
      setSavingStatus(SavingStatus.SAVING);
    } else {
      setSavingStatus(SavingStatus.SAVED);
      savingTimeoutRef.current = setTimeout(() => {
        setSavingStatus(SavingStatus.IDLE);
        clearTimeout(savingTimeoutRef.current);
        savingTimeoutRef.current = null;
      }, 1500);
    }
  }, [updateCount]);

  return (
    <Box
      display="flex"
      flexDirection="column"
      gap={theme.spacing(2)}
      width="100%"
    >
      <Box display="flex" alignItems="center" justifyContent="space-between">
        {/* Left Section - Back Button and Project Selector */}
        <Box display="flex" alignItems="center" gap={theme.spacing(2)}>
          <CustomBackButton
            variant="outlined"
            sx={{
              color: "text.primary",
              padding: theme.spacing(0.125, 1.5),
              fontWeight: "fontWeightMedium",
            }}
            startIcon={
              <Iconify
                icon="formkit:left"
                width={16}
                height={16}
                color={"text.primary"}
              />
            }
            onClick={handleBack}
          >
            Back
          </CustomBackButton>

          <TestsDropdownButton
            ref={testSelectDropDownRef}
            onClick={() => setTestSelectDropDownOpen(true)}
            endIcon={
              isLoadingTests ? (
                <CircularProgress size={16} />
              ) : (
                <Iconify icon="eva:chevron-down-fill" />
              )
            }
          >
            <Typography variant="body2" noWrap>
              {testData?.name || "Select a test run"}
            </Typography>
          </TestsDropdownButton>
          <Typography typography="s2" color="text.disabled">
            {savingStatus === SavingStatus.SAVING
              ? "Saving..."
              : savingStatus === SavingStatus.SAVED
                ? "Saved"
                : ""}
          </Typography>

          {/* Project Dropdown Popover */}
          <Popover
            open={testSelectDropDownOpen}
            anchorEl={testSelectDropDownRef.current}
            onClose={() => setTestSelectDropDownOpen(false)}
            anchorOrigin={{
              vertical: "bottom",
              horizontal: "left",
            }}
            transformOrigin={{
              vertical: "top",
              horizontal: "left",
            }}
            PaperProps={{
              sx: {
                minWidth: testSelectDropDownRef.current?.clientWidth || 227,
                maxWidth: 400,
              },
            }}
          >
            <Box>
              <FormSearchField
                placeholder="Search Tests..."
                size="small"
                searchQuery={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                fullWidth
                autoFocus
                sx={{
                  margin: theme.spacing(1),
                  width: `calc(100% - ${theme.spacing(2)})`,
                }}
                InputProps={{}}
              />
              <Typography
                sx={{
                  paddingX: theme.spacing(1),
                  paddingBottom: theme.spacing(0.5),
                  fontSize: 12,
                  fontWeight: 600,
                  color: "text.disabled",
                }}
              >
                All Test Runs
              </Typography>
              <Box
                sx={{ height: "220px", overflowY: "auto" }}
                ref={scrollContainer}
              >
                {isFetchingTestList && testsList.length === 0 ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <Box key={i} sx={{ px: 1, py: 0.75 }}>
                      <Skeleton
                        variant="rectangular"
                        sx={{
                          borderRadius: 0.5,
                        }}
                        width={`${70 + (i % 3) * 10}%`}
                        height={24}
                      />
                    </Box>
                  ))
                ) : !isFetchingTestList && testsList.length === 0 ? (
                  <Box sx={{ padding: 2, textAlign: "center" }}>
                    <Typography variant="body2" color="text.secondary">
                      {searchText
                        ? "No projects found"
                        : "No projects available"}
                    </Typography>
                  </Box>
                ) : (
                  <>
                    {testsList.map(({ id, name }) => (
                      <MenuItem
                        key={id}
                        onClick={() => {
                          handleTestSelect(id);
                          setTestSelectDropDownOpen(false);
                        }}
                        selected={id === testId}
                        sx={{
                          backgroundColor:
                            id === testId ? "action.selected" : "transparent",
                          "&:hover": {
                            backgroundColor: "action.hover",
                          },
                        }}
                      >
                        <Typography variant="body2" noWrap>
                          {name}
                        </Typography>
                      </MenuItem>
                    ))}
                    {isFetchingTestListNextPage &&
                      Array.from({ length: 2 }).map((_, i) => (
                        <Box key={`skeleton-${i}`} sx={{ px: 1, py: 0.75 }}>
                          <Skeleton
                            variant="rectangular"
                            sx={{
                              borderRadius: 0.5,
                            }}
                            width={`${70 + (i % 3) * 10}%`}
                            height={24}
                          />
                        </Box>
                      ))}
                  </>
                )}
              </Box>
            </Box>
          </Popover>
        </Box>

        {/* Right Section - Auto Refresh, Export, Configure, Share */}
        <Box display="flex" alignItems="center">
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              flexDirection: "row",
              gap: theme.spacing(1.5),
            }}
          >
            {isPromptSimulation ? (
              <>
                <Button
                  variant="outlined"
                  size="small"
                  disabled
                  sx={{
                    minWidth: 280,
                    maxWidth: 280,
                    justifyContent: "flex-start",
                  }}
                  startIcon={
                    <SvgColor src="/assets/icons/navbar/ic_project.svg" />
                  }
                >
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      overflow: "hidden",
                      flex: 1,
                    }}
                  >
                    <Box
                      sx={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      Prompt Template:&nbsp;
                      {isPendingTestDetail ? (
                        <Skeleton
                          variant="text"
                          width={100}
                          height={20}
                          sx={{
                            display: "inline-block",
                            verticalAlign: "middle",
                          }}
                        />
                      ) : (
                        (() => {
                          const promptTemplateName =
                            testData?.prompt_template_detail?.name ??
                            testData?.promptTemplateDetail?.name;
                          if (!promptTemplateName) return "-";
                          return promptTemplateName.length > 25
                            ? promptTemplateName.slice(0, 25) + "..."
                            : promptTemplateName;
                        })()
                      )}
                    </Box>
                  </Box>
                </Button>
                <Button variant="outlined" size="small" disabled>
                  Version:&nbsp;
                  {isPendingTestDetail ? (
                    <Skeleton
                      variant="text"
                      width={15}
                      height={20}
                      sx={{ display: "inline-block", verticalAlign: "middle" }}
                    />
                  ) : (
                    (() => {
                      const tv =
                        testData?.prompt_version_detail?.template_version ??
                        testData?.promptVersionDetail?.templateVersion;
                      if (!tv) return "-";
                      return getVersionLabel(tv);
                    })()
                  )}
                </Button>
              </>
            ) : (
              <>
                <Button
                  variant="outlined"
                  size="small"
                  disabled={
                    !RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role]
                  }
                  sx={{
                    minWidth: 280,
                    maxWidth: 280,
                    justifyContent: "flex-start",
                  }}
                  onClick={() => setAgentDefinitionPopoverOpen(true)}
                  startIcon={
                    <SvgColor src="/assets/icons/navbar/ic_project.svg" />
                  }
                  ref={agentDefinitionPopoverRef}
                >
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      overflow: "hidden",
                      flex: 1,
                    }}
                  >
                    <Box
                      sx={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      Agent Definition:&nbsp;
                      {isPendingTestDetail ? (
                        <Skeleton
                          variant="text"
                          width={100}
                          height={20}
                          sx={{
                            display: "inline-block",
                            verticalAlign: "middle",
                          }}
                        />
                      ) : (
                        (() => {
                          const name =
                            selectedAgentDefinition?.agent_name ??
                            selectedAgentDefinition?.agentName;
                          if (!name) return "-";
                          return name.length > 30
                            ? name.slice(0, 30) + "..."
                            : name;
                        })()
                      )}
                    </Box>
                  </Box>
                </Button>
                <Suspense fallback={null}>
                  <AgentDefinitionPopover
                    open={agentDefinitionPopoverOpen}
                    onClose={() => setAgentDefinitionPopoverOpen(false)}
                    anchor={agentDefinitionPopoverRef.current}
                    simulationType={agentType}
                  />
                </Suspense>
                <Button
                  variant="outlined"
                  size="small"
                  disabled={
                    !RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role]
                  }
                  onClick={() => setAgentDefinitionVersionPopoverOpen(true)}
                  ref={agentDefinitionVersionPopoverRef}
                >
                  Version:&nbsp;
                  {isPendingTestDetail ? (
                    <Skeleton
                      variant="text"
                      width={15}
                      height={20}
                      sx={{ display: "inline-block", verticalAlign: "middle" }}
                    />
                  ) : (
                    selectedAgentDefinitionVersion?.label || "-"
                  )}
                </Button>
                <Suspense fallback={null}>
                  <AgentDefinitionVersionPopover
                    open={agentDefinitionVersionPopoverOpen}
                    onClose={() => setAgentDefinitionVersionPopoverOpen(false)}
                    anchor={agentDefinitionVersionPopoverRef.current}
                    selectedAgent={selectedAgentDefinition}
                  />
                </Suspense>
              </>
            )}
            <Button
              variant="outlined"
              size="small"
              onClick={handleEvalClick}
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
              Evals ({testData?.evals?.length})
            </Button>
            {/* <Button
              variant="outlined"
              size="small"
              disabled={
                !RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role]
              }
              onClick={() => setSimulatorPopoverOpen(true)}
              sx={{ whiteSpace: "nowrap", minWidth: "fit-content" }}
              ref={simulatorPopoverRef}
              startIcon={
                <SvgColor
                  src="/assets/icons/navbar/ic_optimize.svg"
                  sx={{ width: "16px", height: "16px" }}
                />
              }
            >
              Simulator Agent: {selectedSimulatorAgent?.name}
              <Box
                sx={{
                  display: "inline-block",
                  maxWidth: "5ch",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {testData?.simulatorAgent?.name}
              </Box>
            </Button>
            <SimulatorPopover
              open={simulatorPopoverOpen}
              onClose={() => {
                setSimulatorPopoverOpen(false);
              }}
              anchor={simulatorPopoverRef.current}
            /> */}
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

export default React.memo(TestDetailHeader);
