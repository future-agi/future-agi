import React, { memo, useMemo } from "react";
import {
  Box,
  Typography,
  Divider,
  IconButton,
  Stack,
  keyframes,
} from "@mui/material";
import SvgColor from "src/components/svg-color";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { format } from "date-fns";
import CustomTooltip from "../../../components/tooltip/CustomTooltip";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import Iconify from "src/components/iconify";

const MinimumTotalCallsForCriticalIssue = 5;

const MemoizedBarsIcon = memo(() => (
  <Iconify icon="svg-spinners:bars-scale" width={20} height={20} />
));

MemoizedBarsIcon.displayName = "MemoizedBarsIcon";

const spin = keyframes`
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
`;
// pending running completed failed
const CriticalIssueStatus = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  INSUFFICIENT_DATA: "insufficient_data",
};

const CriticalIssueRefetchStates = [
  CriticalIssueStatus.PENDING,
  CriticalIssueStatus.RUNNING,
];

const confidenceMapping = {
  high: {
    icon: "ic_critical",
    bgColor: "red.o10",
    color: "red.500",
  },
  mid: {
    icon: "ic_critical",
    bgColor: "red.o10",
    color: "red.500",
  },
  low: {
    icon: "ic_critical",
    bgColor: "red.o10",
    color: "red.500",
  },
};

const CustomTooltipTitle = ({ value }) => {
  return (
    <Box
      sx={{
        borderRadius: 1,
        maxWidth: "259px",
      }}
    >
      <Typography
        typography={"s2"}
        fontWeight={"fontWeightRegular"}
        color={"text.primary"}
      >
        <Typography
          typography={"s2"}
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
          component={"span"}
        >
          Evidence:
        </Typography>{" "}
        {value}
      </Typography>
    </Box>
  );
};

CustomTooltipTitle.propTypes = {
  value: PropTypes.string,
};

const CriticalIssues = ({ mode = "develop" }) => {
  const { dataset, executionId } = useParams();

  const queryKey =
    mode === "develop"
      ? ["critical-issue", dataset]
      : ["critical-issue-simulate", executionId];

  const queryEndpoint =
    mode === "develop"
      ? endpoints.dataset.criticalIssue(dataset)
      : endpoints.testExecutions.criticalIssue(executionId);

  const { data: kpis, isLoading: isKpisLoading } = useQuery({
    queryKey: ["test-execution-detail", executionId],
    queryFn: () => axios.get(endpoints.testExecutions.kpis(executionId)),
    select: (data) => data.data,
    enabled: mode === "simulate" && Boolean(executionId),
  });

  const isCriticalIssueFetchingAllowed =
    mode === "simulate"
      ? kpis?.total_calls >= MinimumTotalCallsForCriticalIssue
      : true;

  const { data, isLoading, refetch, isRefetching } = useQuery({
    queryKey,
    queryFn: () => axios.get(queryEndpoint),
    select: (d) => {
      return {
        result: d?.data?.result?.response,
        lastUpdated:
          d?.data?.result?.last_updated ?? d?.data?.result?.lastUpdated,
        status: d?.data?.result?.status,
        rowCount: d?.data?.result?.row_count ?? d?.data?.result?.rowCount,
        minRowsRequired:
          d?.data?.result?.min_rows_required ??
          d?.data?.result?.minRowsRequired,
      };
    },
    enabled: () => {
      if (mode === "develop") {
        return Boolean(dataset);
      } else if (mode === "simulate") {
        return Boolean(executionId) && isCriticalIssueFetchingAllowed;
      }
    },
    refetchOnWindowFocus: false,
    refetchInterval: ({ state }) => {
      const result = state?.data?.data?.result;
      if (CriticalIssueRefetchStates.includes(result?.status)) {
        return 5000;
      }
      return false;
    },
  });

  const mutationEndpoint =
    mode === "develop"
      ? endpoints.dataset.criticalIssueRefresh(dataset)
      : endpoints.testExecutions.criticalIssueRefresh(executionId);

  const { mutate: refreshCriticalIssue, isPending: isRefetchingCriticalIssue } =
    useMutation({
      mutationFn: () => axios.post(mutationEndpoint),
      onSuccess: () => {
        refetch();
      },
    });

  const isReconfiguring =
    isLoading ||
    isRefetching ||
    isRefetchingCriticalIssue ||
    CriticalIssueRefetchStates.includes(data?.status) ||
    isKpisLoading;

  const issues = useMemo(() => {
    const issues = [];
    Object.entries(data?.result || {}).forEach(([_key, value]) => {
      if (!Array.isArray(value)) return;
      value
        ?.filter((t) => t.kind === "failure")
        .forEach((temp) => {
          issues.push({
            type: temp?.confidence,
            title: temp?.theme,
            guidance: temp?.guidance,
            evidenceSummary: temp?.evidence_summary,
          });
        });
    });
    return issues;
  }, [data?.result]);

  return (
    <Box
      sx={{
        padding: 2,
        height: "100%",
        width: "100%",
        backgroundColor: "background.neutral",
      }}
    >
      <Stack
        direction={"row"}
        justifyContent={"space-between"}
        alignItems={"center"}
      >
        <Typography
          typography="s1"
          fontWeight={"fontWeightMedium"}
          color="text.primary"
        >
          Critical issues{" "}
          <Typography
            component={"span"}
            typography={"s1"}
            fontWeight={"fontWeightRegular"}
            color={"text.primary"}
          >
            (How to solve it)
          </Typography>
        </Typography>
        <ShowComponent condition={!isLoading && isCriticalIssueFetchingAllowed}>
          <IconButton
            disabled={isLoading}
            size="small"
            onClick={() => {
              refreshCriticalIssue();
            }}
            sx={{
              display: "flex",
              flexDirection: "row",
              gap: 1,
              fontWeight: "fontWeightMedium",
              borderRadius: "8px",
            }}
          >
            <SvgColor
              // @ts-ignore
              src="/assets/icons/ic_refresh.svg"
              sx={{
                height: 20,
                width: 20,
                color: "primary.main",
                animation: isReconfiguring
                  ? `${spin} 1s linear infinite`
                  : "none",
              }}
            />
            <Typography
              typography="s1"
              color={"primary.main"}
              fontWeight={"fontWeightMedium"}
            >
              Refresh
            </Typography>
          </IconButton>
        </ShowComponent>
      </Stack>
      {data?.lastUpdated && (
        <Stack direction={"row"} gap={0.5} alignItems={"center"}>
          <SvgColor
            // @ts-ignore
            src="/assets/icons/ic_reload.svg"
            sx={{
              height: 11,
              width: 11,
              color: "text.disabled",
            }}
          />
          <Typography
            typography="s2"
            fontWeight={"fontWeightRegular"}
            color={"text.disabled"}
          >
            Updated {format(new Date(data?.lastUpdated), "dd/MM/yyyy, hh:mm a")}
          </Typography>
        </Stack>
      )}

      <Box
        marginTop={1.5}
        display="flex"
        gap={1.5}
        flexDirection={"column"}
        overflow={"auto"}
        height={"337px"}
        pb={2}
      >
        {isReconfiguring ? (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 1.5,
              width: "100%",
              height: "100%",
            }}
          >
            <MemoizedBarsIcon />
            <Typography typography="s1">
              Digging for critical issues... grab a coffee, this might take a
              moment!
            </Typography>
          </Box>
        ) : data?.status === CriticalIssueStatus.INSUFFICIENT_DATA &&
          mode === "develop" ? (
          <Typography
            typography={"s1"}
            color={"text.primary"}
            fontWeight={"fontWeightRegular"}
          >
            {`At least ${data?.minRowsRequired || 15} data rows are needed for critical issues analysis. Your dataset currently has ${data?.rowCount ?? 0} rows.`}
          </Typography>
        ) : !issues || issues?.length === 0 ? (
          <Typography
            typography={"s1"}
            color={"text.primary"}
            fontWeight={"fontWeightRegular"}
          >
            {isCriticalIssueFetchingAllowed
              ? `Our analysis didn't find any clusters of similar failures. This
              may mean issues are rare, inconsistent, or below the current
              threshold.`
              : `Minimum ${MinimumTotalCallsForCriticalIssue} calls are required to fetch critical issues.`}
          </Typography>
        ) : (
          issues?.map((issue, index) => {
            return (
              <Box
                key={index}
                display="flex"
                gap={1.5}
                flexDirection={"column"}
              >
                {index != 0 && (
                  <Divider
                    orientation="horizontal"
                    sx={{
                      borderColor: "divider",
                    }}
                  />
                )}
                <CustomTooltip
                  placement="bottom"
                  show
                  title={<CustomTooltipTitle value={issue?.evidenceSummary} />}
                  slotProps={{
                    popper: {
                      modifiers: [
                        {
                          name: "offset",
                          options: {
                            offset: [0, -25],
                          },
                        },
                      ],
                    },
                  }}
                  arrow
                >
                  <Box
                    display={"flex"}
                    gap={1}
                    justifyContent={"space-between"}
                  >
                    <Box display={"flex"} gap={1.5} alignItems={"flex-start"}>
                      <IconButton
                        sx={{
                          borderRadius: "4px",
                          height: "32px",
                          width: "32px",
                          padding: "4px",
                          cursor: "default",
                          backgroundColor: confidenceMapping["high"]?.bgColor,
                        }}
                      >
                        <SvgColor
                          // @ts-ignore
                          src={`/assets/icons/${confidenceMapping["high"]?.icon}.svg`}
                          sx={{
                            color: confidenceMapping["high"]?.color,
                            height: "16px",
                            width: "16px",
                          }}
                        />
                      </IconButton>
                      <Stack>
                        <Typography
                          typography="s2"
                          fontWeight={"fontWeightMedium"}
                          color={"text.primary"}
                        >
                          {issue?.title}
                        </Typography>
                        <Typography
                          typography="s2"
                          fontWeight={"fontWeightRegular"}
                          color="text.primary"
                        >
                          {issue?.guidance}
                        </Typography>
                      </Stack>
                    </Box>
                    {/* <SvgColor
                    // @ts-ignore
                    src={"/assets/icons/custom/lucide--chevron-right.svg"}
                  /> */}
                  </Box>
                </CustomTooltip>
              </Box>
            );
          })
        )}
      </Box>
    </Box>
  );
};

CriticalIssues.propTypes = {
  mode: PropTypes.string,
};

export default CriticalIssues;
