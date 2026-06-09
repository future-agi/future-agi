import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import React, { lazy, Suspense, useState } from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { useMutation } from "@tanstack/react-query";
import useKpis from "src/hooks/useKpis";
import { useNavigate, useParams } from "react-router";
import { enqueueSnackbar } from "src/components/snackbar";
import { useTestDetail } from "./context/TestDetailContext";
import { useCancelExecution } from "../test/common";
import { useTestExecutionStore } from "./states";
import { RerunTestOptions, TestRunLoadingStatus } from "./common";
import { ShowComponent } from "src/components/show";
import {
  useExecutionReproducibility,
  useRerunTest,
} from "src/api/tests/testDetails";
import { LoadingButton } from "@mui/lab";
import { ConfirmDialog } from "src/components/custom-dialog";
import CustomTooltip from "src/components/tooltip";
import { useTestRunSdkStoreShallow } from "../test/TestRuns/state";
const NewVoiceSimulationDrawer = lazy(
  () => import("../test/TestRuns/NewVoiceSimulationDrawer"),
);
import { AGENT_TYPES } from "../agents/constants";
import axios, { endpoints } from "src/utils/axios";
import TestDetailBreadcrumb from "./TestDetailBreadcrumb";
import useTestRunDetails from "src/hooks/useTestRunDetails";
import CallStatus from "../test/CallLogs/CallStatus";
import CallMetadataSection from "./components/CallMetadataSection";
import { formatStartTimeByRequiredFormat } from "src/utils/utils";
import { formatDurationSafe } from "src/components/CallLogsDrawer/CustomCallLogHeader";
import { SourceType } from "../scenarios/common";

const TestRunDetailHeader = () => {
  const theme = useTheme();
  const _navigate = useNavigate();
  const { executionId, testId } = useParams();
  const { refreshGrid } = useTestDetail();
  const { mutate: cancelExecution, isPending: isCancelPending } =
    useCancelExecution();
  const status = useTestExecutionStore((state) => state.status);
  const showStopButton = TestRunLoadingStatus.includes(status);
  const [confirmRerun, setConfirmRerun] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const { setSdkCodeOpen } = useTestRunSdkStoreShallow((state) => ({
    setSdkCodeOpen: state.setSdkCodeOpen,
  }));
  const { mutate: rerunTest, isPending: isRerunPending } = useRerunTest(
    executionId,
    {
      onSuccess: () => {
        setConfirmRerun(false);
        refreshGrid();
      },
    },
  );
  const { data: testData } = useTestRunDetails(testId);
  const { data: kpis } = useKpis(executionId);
  const { data: reproducibility } = useExecutionReproducibility(executionId, {
    enabled: confirmRerun && !!executionId,
  });
  const agentType = kpis?.agent_type;
  const preflight = reproducibility?.preflight;
  const inputDrift = preflight?.input_drift ?? preflight?.inputDrift;
  const diagnosis =
    reproducibility?.score_change_diagnosis ??
    reproducibility?.scoreChangeDiagnosis;
  const preflightIssues = preflight?.issues ?? [];
  const preflightStatus = preflight?.status;
  const inputFingerprint =
    reproducibility?.current?.replay_plan?.input_fingerprint ??
    reproducibility?.current?.replayPlan?.inputFingerprint;
  const isReplayBlocked = ["blocked", "input_drift_blocked"].includes(
    preflightStatus,
  );
  const handleRerun = () => {
    if (agentType === AGENT_TYPES.VOICE) {
      setConfirmRerun(true);
    } else {
      setSdkCodeOpen(true);
    }
  };
  const { mutate: exportLogs } = useMutation({
    mutationFn: () =>
      axios.get(endpoints.runTests.executionDetailsExport(executionId)),
    onSuccess: (response) => {
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `call-logs-${executionId}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      enqueueSnackbar("Execution Details Logs downloaded successfully", {
        variant: "success",
      });
    },
  });

  const handleStop = () => {
    if (!executionId) return;

    //@ts-ignore
    cancelExecution(executionId, {
      onSuccess: () => {
        setConfirmStop(false);
        refreshGrid();
      },
    });
  };
  const formattedStartTime = formatStartTimeByRequiredFormat(
    testData?.created_at,
    "dd-MM-yyyy HH:mm:ss",
  );

  return (
    <Box
      display="flex"
      flexDirection="column"
      gap={theme.spacing(2)}
      width="100%"
    >
      <Box
        display="flex"
        alignItems="flex-start"
        justifyContent="space-between"
        sx={{
          flexDirection: "column",
          gap: 2,
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          <Box>
            <TestDetailBreadcrumb
              items={[
                {
                  label: "Simulated runs",
                  href: `/dashboard/simulate/test/${testId}`,
                },
                {
                  label: `Execution : ${executionId}`,
                  // No href means it's the current page (non-clickable)
                },
              ]}
            />
          </Box>
          <Box>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                flexDirection: "row",
                gap: theme.spacing(1.5),
              }}
            >
              <Button
                variant="outlined"
                size="small"
                onClick={() => exportLogs()}
                startIcon={
                  <SvgColor
                    src="/assets/icons/action_buttons/ic_download.svg"
                    sx={{
                      width: 16,
                      height: 16,
                      color: "text.primary",
                    }}
                  />
                }
              >
                Export Data
              </Button>
              <ShowComponent condition={showStopButton}>
                <LoadingButton
                  variant="outlined"
                  color="primary"
                  size="small"
                  sx={{
                    borderRadius: "4px",
                    borderColor: "primary.main",
                  }}
                  onClick={() => setConfirmStop(true)}
                  startIcon={
                    <Iconify
                      icon="bi:stop-circle"
                      width="14px"
                      height="14px"
                      sx={{
                        cursor: "pointer",
                        marginRight: "-2px",
                      }}
                    />
                  }
                >
                  <Typography variant="s1" fontWeight={"fontWeightSemiBold"}>
                    Stop Running
                  </Typography>
                </LoadingButton>
              </ShowComponent>
              <ShowComponent
                condition={
                  !showStopButton &&
                  (testData?.source_type ?? testData?.sourceType) !==
                    SourceType.PROMPT
                }
              >
                <CustomTooltip
                  show={kpis?.total_calls === 0}
                  title="No completed calls to re-simulate. Run a new simulation first."
                  placement="bottom"
                  arrow
                  size="small"
                  type="black"
                  slotProps={{
                    tooltip: {
                      sx: {
                        maxWidth: "200px !important",
                      },
                    },
                    popper: {
                      modifiers: {
                        name: "preventOverflow",
                        options: {
                          boundary: "viewport",
                          padding: 12,
                        },
                      },
                    },
                  }}
                >
                  <span>
                    <LoadingButton
                      size="small"
                      variant="outlined"
                      sx={{
                        ...theme.typography.s2_1,
                        fontWeight: 500,
                        paddingX: 2,
                      }}
                      startIcon={
                        <SvgColor src="/assets/icons/navbar/ic_get_started.svg" />
                      }
                      onClick={handleRerun}
                      disabled={kpis?.total_calls === 0}
                    >
                      Re-run simulation
                    </LoadingButton>
                  </span>
                </CustomTooltip>
              </ShowComponent>
            </Box>
          </Box>
        </Box>
        <ConfirmDialog
          open={confirmRerun}
          onClose={() => setConfirmRerun(false)}
          onConfirm={() => setConfirmRerun(false)}
          title="Confirm Rerun Test"
          content={
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
              <Typography typography="m3" color="text.secondary">
                This will rerun all calls and evaluations in this execution.
              </Typography>
              <Box
                sx={{
                  border: "1px solid",
                  borderColor:
                    preflightStatus === "ready"
                      ? "success.light"
                      : "warning.light",
                  borderRadius: "4px",
                  padding: 1.25,
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.75,
                }}
              >
                <Typography
                  typography="s2"
                  fontWeight="fontWeightSemiBold"
                  color={
                    preflightStatus === "ready"
                      ? "success.main"
                      : "warning.main"
                  }
                >
                  Replay preflight: {preflightStatus || "checking"}
                </Typography>
                <ShowComponent condition={!!inputFingerprint}>
                  <Typography typography="s2" color="text.secondary">
                    Input fingerprint: {String(inputFingerprint).slice(0, 12)}
                  </Typography>
                </ShowComponent>
                <ShowComponent condition={!!inputDrift?.has_drift}>
                  <Typography typography="s2" color="warning.main">
                    Drift: {(inputDrift?.changed_sections || []).join(", ")}
                  </Typography>
                </ShowComponent>
                <ShowComponent condition={preflightIssues.length > 0}>
                  <Typography typography="s2" color="text.secondary">
                    {preflightIssues[0]?.message}
                  </Typography>
                </ShowComponent>
                <ShowComponent condition={!!diagnosis?.summary}>
                  <Typography typography="s2" color="text.secondary">
                    {diagnosis?.summary}
                  </Typography>
                </ShowComponent>
              </Box>
            </Box>
          }
          action={
            <LoadingButton
              variant="contained"
              color="primary"
              loading={isRerunPending}
              disabled={isReplayBlocked}
              size="small"
              onClick={() => {
                rerunTest({
                  select_all: true,
                  rerun_type: RerunTestOptions.CALL_AND_EVAL,
                  call_execution_ids: [],
                });
              }}
            >
              Confirm
            </LoadingButton>
          }
        />
        <ConfirmDialog
          open={confirmStop}
          onClose={() => setConfirmStop(false)}
          onConfirm={() => setConfirmStop(false)}
          title="Confirm Stop Runs"
          content="This will stop all the runs which are queued and also stop evaluations from running."
          action={
            <LoadingButton
              variant="contained"
              color="primary"
              loading={isCancelPending}
              size="small"
              onClick={() => {
                handleStop();
              }}
            >
              Confirm
            </LoadingButton>
          }
        />

        <Stack
          direction={"row"}
          justifyContent={"space-between"}
          alignItems={"center"}
          sx={{
            width: "100%",
          }}
        >
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: (theme) => theme.spacing(0.25),
            }}
          >
            <Typography
              color="text.primary"
              typography="m3"
              fontWeight={"fontWeightMedium"}
            >
              Execution : {executionId}
            </Typography>
            <Box
              sx={{
                display: "flex",
                gap: (theme) => theme.spacing(0.5),
                alignItems: "center",
              }}
            >
              <CallMetadataSection
                items={[
                  {
                    id: "calls",
                    value: `${kpis?.total_calls} ${kpis?.agent_type === AGENT_TYPES.CHAT ? "Chats" : "Calls"} analyzed`,
                    showDivider: false,
                  },
                  {
                    id: "scenarios",
                    condition: testData?.scenarios?.length > 0,
                    value: `Scenarios: ${testData?.scenarios?.length || 0}`,
                  },
                  {
                    id: "phone",
                    condition:
                      ((testData?.agent_version ?? testData?.agentVersion)
                        ?.configuration_snapshot?.contact_number ?? null) !==
                        null && agentType === AGENT_TYPES.VOICE,
                    value: `Phone Number: ${
                      (testData?.agent_version ?? testData?.agentVersion)
                        ?.configuration_snapshot?.contact_number || 0
                    }`,
                  },
                  {
                    id: "time",
                    condition: testData?.created_at && !!formattedStartTime,
                    value: `Run Start: ${formattedStartTime}`,
                  },
                  {
                    id: "run-start",
                    condition: true,
                    value: `${formatDurationSafe(kpis?.totalDuration || 0)}`,
                    iconSrc: "/assets/icons/ic_clock.svg",
                    iconSx: { width: 12 },
                  },
                  {
                    id: "direction",
                    condition:
                      agentType !== AGENT_TYPES.CHAT && kpis?.isInbound != null,
                    value: kpis?.isInbound === false ? "Outbound" : "Inbound",
                    variant: "chip",
                    iconSrc:
                      kpis?.isInbound === false
                        ? "/assets/icons/ic_call_outbound.svg"
                        : "/assets/icons/ic_call_inbound.svg",
                  },
                  {
                    id: "status",
                    condition: status !== null,
                    value: <CallStatus value={status} />,
                  },
                ]}
              />
            </Box>
          </Box>
          {/* Right Section - Auto Refresh, Export, Configure, Share */}
          <Box display="flex" alignItems="center"></Box>
        </Stack>
      </Box>
      <Suspense fallback={null}>
        <NewVoiceSimulationDrawer />
      </Suspense>
    </Box>
  );
};

export default React.memo(TestRunDetailHeader);
