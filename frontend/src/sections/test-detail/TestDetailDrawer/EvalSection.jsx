import {
  Box,
  Chip,
  IconButton,
  styled,
  Typography,
  useTheme,
} from "@mui/material";
import React, { memo } from "react";
import { useTestDetailSideDrawerStoreShallow } from "../states";
import CellMarkdown from "../../common/CellMarkdown";
import { ShowComponent } from "../../../components/show";
import { getLabel, getStatusColor } from "../../develop-detail/DataTab/common";
import AudioErrorCard from "src/components/custom-audio/AudioErrorCard";
import ErrorLocalizeCard from "src/sections/common/ErrorLocalizeCard";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { canonicalEntries } from "src/utils/utils";

const WrapperBox = styled(Box)(({ theme }) => ({
  display: "flex",
  justifyContent: "center",
  alignItems: "center",
  minHeight: "300px",
  border: "1px solid",
  borderColor: theme.palette.divider,
  borderRadius: "4px",
  padding: "16px",
  gap: theme.spacing(1),
}));

const MemoizedBarsIcon = memo(() => (
  <Iconify icon="svg-spinners:bars-scale" width={20} height={20} />
));

MemoizedBarsIcon.displayName = "MemoizedBarsIcon";

const ERROR_LOCALIZER_TASK_STATUS = {
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
};

const ERROR_LOCALIZER_REFETCH_STATUS = [ERROR_LOCALIZER_TASK_STATUS.RUNNING];

const ERROR_LOCALIZER_REFETCH_INTERVAL = 5000;

const EvalDrawerSection = () => {
  const { evalView, setEvalView } = useTestDetailSideDrawerStoreShallow(
    (s) => ({ evalView: s.evalView, setEvalView: s.setEvalView }),
  );

  const reason = evalView?.metricDetail?.reason;
  const isError = evalView?.metricDetail?.error;
  const output = evalView?.metricDetail?.value;
  const theme = useTheme();
  const { testDetailDrawerOpenId } = useTestDetailSideDrawerStoreShallow(
    (state) => ({
      testDetailDrawerOpenId: state.testDetailDrawerOpen?.id,
    }),
  );

  const isErrorLocalizerEnabled = evalView?.metricDetail?.errorLocalizer;

  const { data: errorLocalizerDataList } = useQuery({
    queryKey: ["error-localizer-tasks", testDetailDrawerOpenId],
    queryFn: () =>
      axios.get(
        endpoints.testExecutions.getErrorLocalizerTasks(testDetailDrawerOpenId),
        {
          params: {
            eval_config_id: evalView?.metricDetail?.id,
          },
        },
      ),
    select: (data) => data?.data?.errorLocalizerTasks || [],
    refetchInterval: ({ state }) => {
      const errorLocalizerTasks = state?.data?.data?.errorLocalizerTasks;
      const errorLocalizerTask = errorLocalizerTasks?.find(
        (task) => task.evalConfigId === evalView?.metricDetail?.id,
      );
      if (ERROR_LOCALIZER_REFETCH_STATUS.includes(errorLocalizerTask?.status)) {
        return ERROR_LOCALIZER_REFETCH_INTERVAL;
      }
      return false;
    },
    enabled: Boolean(testDetailDrawerOpenId && isErrorLocalizerEnabled),
  });

  const errorLocalizerTask = errorLocalizerDataList?.find(
    (task) => task.evalConfigId === evalView?.metricDetail?.id,
  );
  const errorLocalizerTaskStatus = errorLocalizerTask?.status;
  const errorAnalysis = errorLocalizerTask?.errorAnalysis;
  const isErrorLocalizerTaskRunning = ERROR_LOCALIZER_REFETCH_STATUS.includes(
    errorLocalizerTaskStatus,
  );
  const isErrorLocalizerTaskFailed =
    errorLocalizerTaskStatus === ERROR_LOCALIZER_TASK_STATUS.FAILED;

  return (
    <Box
      sx={{
        width: 500,
        padding: 2,
        borderRight: "1px solid",
        borderColor: "divider",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 2,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography typography="m2" fontWeight="fontWeightSemiBold">
          {evalView?.metricDetail?.name}
        </Typography>
        <IconButton onClick={() => setEvalView(null)}>
          <Iconify icon="akar-icons:cross" />
        </IconButton>
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          overflowY: "auto",
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <Typography typography="s1" fontWeight="fontWeightSemiBold">
            Score
          </Typography>
          <Box>
            {isError ? (
              <Box sx={{ color: theme.palette.error.main, fontSize: "14px" }}>
                Error
              </Box>
            ) : (
              output &&
              output !== "" && (
                <>
                  <ShowComponent condition={!Array.isArray(output)}>
                    <Chip
                      variant="soft"
                      label={getLabel(output)}
                      size="small"
                      sx={{
                        ...getStatusColor(output, theme),
                        transition: "none",
                        "&:hover": {
                          backgroundColor: getStatusColor(output, theme)
                            .backgroundColor, // Lock it to same color
                          boxShadow: "none",
                        },
                      }}
                    />
                  </ShowComponent>
                  {Array.isArray(output) && (
                    <>
                      <ShowComponent condition={output?.length === 0}>
                        <Chip
                          variant="soft"
                          label={"None"}
                          size="small"
                          sx={{
                            backgroundColor: theme.palette.red.o10,
                            color: theme.palette.red[500],
                            marginRight: "10px",
                            transition: "none",
                            "&:hover": {
                              backgroundColor: theme.palette.red[500], // Lock it to same color
                              boxShadow: "none",
                            },
                          }}
                        />
                      </ShowComponent>
                      <ShowComponent condition={output?.length > 0}>
                        {output?.map((val) => (
                          <Chip
                            key={val}
                            variant="soft"
                            label={val}
                            size="small"
                            sx={{
                              ...getStatusColor(output, theme),
                              marginRight: theme.spacing(1),
                              transition: "none",
                              "&:hover": {
                                backgroundColor: getStatusColor(output, theme)
                                  .backgroundColor, // Lock it to same color
                                boxShadow: "none",
                              },
                            }}
                          />
                        ))}
                      </ShowComponent>
                    </>
                  )}
                </>
              )
            )}
          </Box>
        </Box>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <Typography typography="s1" fontWeight="fontWeightSemiBold">
            Explanation
          </Typography>
          <Box
            sx={{
              border: "1px solid var(--border-default)",
              padding: "16px",
              borderRadius: "4px",
            }}
          >
            {reason?.trim() ? (
              <CellMarkdown spacing={0} text={reason} />
            ) : (
              "Unable to fetch Explanation"
            )}
          </Box>
        </Box>
        <ShowComponent condition={!isErrorLocalizerEnabled}>
          <WrapperBox>
            <Typography typography="s2" sx={{ textAlign: "center" }}>
              You have not enabled error localizer for this evaluation. Please
              enable error localizer by clicking on three dots and re
              configuring eval
            </Typography>
          </WrapperBox>
        </ShowComponent>
        <ShowComponent condition={isErrorLocalizerEnabled}>
          <ShowComponent condition={isErrorLocalizerTaskRunning}>
            <WrapperBox>
              <MemoizedBarsIcon />
              <Typography typography="s2">
                Investigating why this evaluation failed... this might take a
                moment!
              </Typography>
            </WrapperBox>
          </ShowComponent>
          <ShowComponent condition={isErrorLocalizerTaskFailed}>
            <WrapperBox>
              <Typography typography="s2" sx={{ textAlign: "center" }}>
                There was an error while investigating why this evaluation
                failed. We will get to the root of this.
              </Typography>
            </WrapperBox>
          </ShowComponent>
          <ShowComponent
            condition={
              errorAnalysis &&
              errorAnalysis?.input1?.length &&
              !isErrorLocalizerTaskRunning &&
              !isErrorLocalizerTaskFailed
            }
          >
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: "8px",
                height: "100%",
                marginY: "15px",
                overflowWrap: "break-word",
                position: "relative",
              }}
            >
              <Typography
                fontWeight={"fontWeightMedium"}
                variant="s1"
                color="text.primary"
              >
                Possible Error
              </Typography>
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  overflowY: "auto",
                }}
              >
                {errorAnalysis &&
                  (() => {
                    const errorAnalysisEntries =
                      canonicalEntries(errorAnalysis);
                    const hasOrgSegment = errorAnalysisEntries
                      .map(([, value]) => value)
                      .flat()
                      .some((entry) => entry?.orgSegment);

                    if (hasOrgSegment) {
                      return (
                        <AudioErrorCard
                          valueInfos={errorLocalizerTask}
                          column={errorLocalizerTask?.selectedInputKey}
                        />
                      );
                    }

                    return errorAnalysisEntries
                      .filter(([_, value]) => value?.length)
                      .map(([key, value]) => (
                        <ErrorLocalizeCard
                          key={key}
                          value={value}
                          column={errorLocalizerTask?.selectedInputKey}
                          tabValue="raw"
                          datapoint={errorLocalizerTask}
                        />
                      ));
                  })()}
              </Box>
            </Box>
          </ShowComponent>
        </ShowComponent>
      </Box>
    </Box>
  );
};

export default EvalDrawerSection;
