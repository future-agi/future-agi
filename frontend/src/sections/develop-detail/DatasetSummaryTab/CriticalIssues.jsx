import React, { memo, useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Collapse,
  Typography,
  Divider,
  IconButton,
  Stack,
  keyframes,
} from "@mui/material";
import SvgColor from "src/components/svg-color";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import { useNavigate } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import CustomTooltip from "../../../components/tooltip/CustomTooltip";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import Iconify from "src/components/iconify";
import RelativeTime from "src/sections/common/ux/RelativeTime";

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
    bgColor: "status.danger.bg",
    color: "status.danger.fg",
    label: "High confidence",
  },
  mid: {
    icon: "ic_critical",
    bgColor: "status.warning.bg",
    color: "status.warning.fg",
    label: "Medium confidence",
  },
  low: {
    icon: "ic_critical",
    bgColor: "action.hover",
    color: "text.secondary",
    label: "Low confidence",
  },
};

const smallChip = {
  height: 20,
  typography: "s3",
  borderRadius: "4px",
  "& .MuiChip-label": { px: 0.75 },
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

/**
 * One cluster: icon, theme, guidance, confidence / eval / trigger chips.
 * Failure clusters open the Data tab filtered to that eval's failing rows.
 */
const IssueRow = ({ issue, first, success = false, onOpen }) => {
  const conf = confidenceMapping[issue.type] ?? confidenceMapping.mid;
  const clickable = Boolean(onOpen);
  return (
    <Box display="flex" gap={1.5} flexDirection={"column"}>
      {!first && (
        <Divider orientation="horizontal" sx={{ borderColor: "divider" }} />
      )}
      <CustomTooltip
        card
        slotProps={{
          popper: {
            modifiers: [{ name: "offset", options: { offset: [0, -25] } }],
          },
        }}
        placement="bottom"
        show={Boolean(issue.evidenceSummary)}
        title={<CustomTooltipTitle value={issue.evidenceSummary} />}
        arrow
      >
        <Box
          role={clickable ? "button" : undefined}
          tabIndex={clickable ? 0 : undefined}
          onClick={onOpen ?? undefined}
          onKeyDown={(e) => {
            if (clickable && (e.key === "Enter" || e.key === " ")) {
              e.preventDefault();
              onOpen();
            }
          }}
          sx={{
            display: "flex",
            gap: 1.5,
            alignItems: "flex-start",
            borderRadius: "6px",
            p: 0.5,
            mx: -0.5,
            cursor: clickable ? "pointer" : "default",
            "&:hover": clickable ? { bgcolor: "action.hover" } : undefined,
          }}
        >
          <Box
            sx={{
              borderRadius: "4px",
              height: "32px",
              width: "32px",
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: success ? "status.success.bg" : conf.bgColor,
            }}
          >
            {success ? (
              <Iconify
                icon="qlementine-icons:success-16"
                width={16}
                sx={{ color: "status.success.fg" }}
              />
            ) : (
              <SvgColor
                // @ts-ignore
                src={`/assets/icons/${conf.icon}.svg`}
                sx={{ color: conf.color, height: "16px", width: "16px" }}
              />
            )}
          </Box>
          <Stack sx={{ flex: 1, minWidth: 0 }} gap={0.5}>
            <Typography
              typography="s2"
              fontWeight={"fontWeightMedium"}
              color={"text.primary"}
            >
              {issue.title}
            </Typography>
            <Typography
              typography="s2"
              fontWeight={"fontWeightRegular"}
              color="text.primary"
            >
              {issue.guidance}
            </Typography>
            <Stack
              direction="row"
              gap={0.5}
              flexWrap="wrap"
              alignItems="center"
            >
              {!success && (
                <Chip
                  size="small"
                  label={conf.label}
                  sx={{
                    ...smallChip,
                    bgcolor: conf.bgColor,
                    color: conf.color,
                  }}
                />
              )}
              {issue.evalName && (
                <Chip
                  size="small"
                  variant="outlined"
                  label={issue.evalName}
                  sx={smallChip}
                />
              )}
              {issue.triggers.map((t) => (
                <Chip
                  key={t}
                  size="small"
                  label={t}
                  sx={{
                    ...smallChip,
                    bgcolor: "action.hover",
                    color: "text.secondary",
                  }}
                />
              ))}
            </Stack>
          </Stack>
          {clickable && (
            <Stack
              direction="row"
              alignItems="center"
              gap={0.25}
              sx={{ flexShrink: 0, color: "primary.main", pt: 0.25 }}
            >
              <Typography typography="s2" fontWeight={500} color="primary.main">
                {issue.rowCount != null
                  ? `${issue.rowCount} ${issue.rowCount === 1 ? "row" : "rows"}${issue.evalName && issue.link?.count ? ` · all ${issue.link.count} ${issue.evalName} fails` : ""}`
                  : issue.link?.count
                    ? `Failing rows (${issue.link.count})`
                    : "View rows"}
              </Typography>
              <Iconify icon="mingcute:right-line" width={16} />
            </Stack>
          )}
        </Box>
      </CustomTooltip>
    </Box>
  );
};

IssueRow.propTypes = {
  issue: PropTypes.object.isRequired,
  first: PropTypes.bool,
  success: PropTypes.bool,
  onOpen: PropTypes.func,
};

const CriticalIssues = ({ mode = "develop", evalLinks }) => {
  const navigate = useNavigate();
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
      // The contract body is an empty object; an absent body fails the dev
      // request guard before the request is sent.
      mutationFn: () => axios.post(mutationEndpoint, {}),
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

  const { issues, successes } = useMemo(() => {
    const issues = [];
    const successes = [];
    Object.entries(data?.result || {}).forEach(([_key, value]) => {
      if (!Array.isArray(value)) return;
      value.forEach((temp) => {
        const item = {
          type: temp?.confidence,
          title: temp?.theme,
          guidance: temp?.guidance,
          evidenceSummary: temp?.evidence_summary,
          triggers: temp?.triggers ?? [],
          evalName: temp?.eval_name,
          rowCount: temp?.row_count ?? null,
          exampleRows: temp?.example_rows ?? [],
          link: evalLinks?.[temp?.eval_config_id] ?? null,
        };
        if (temp.kind === "failure") issues.push(item);
        else if (temp.kind === "success") successes.push(item);
      });
    });
    return { issues, successes };
  }, [data?.result, evalLinks]);
  const [showWorks, setShowWorks] = useState(false);

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
          Top failure patterns{" "}
          <Typography
            component={"span"}
            typography={"s1"}
            fontWeight={"fontWeightRegular"}
            color={"text.primary"}
          >
            · suggested fixes
          </Typography>
          <Box
            component="span"
            sx={{
              ml: 1,
              px: 0.75,
              py: 0.25,
              borderRadius: 0.5,
              border: "1px solid",
              borderColor: "divider",
              typography: "s3",
              color: "text.secondary",
              verticalAlign: "middle",
            }}
            title="Found by rules over this dataset's runs and evals"
          >
            Rule-based
          </Box>
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
            {isReconfiguring ? (
              "Refreshing…"
            ) : (
              <>
                Updated <RelativeTime iso={data?.lastUpdated} />
              </>
            )}
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
          <>
            {issues.map((issue, index) => (
              <IssueRow
                key={`${issue.evalName}-${issue.title}`}
                issue={issue}
                first={index === 0}
                onOpen={
                  issue.link?.href ? () => navigate(issue.link.href) : null
                }
              />
            ))}
            {successes.length > 0 && (
              <Box>
                <Divider sx={{ borderColor: "divider", mb: 1 }} />
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => setShowWorks((v) => !v)}
                  startIcon={
                    <Iconify
                      icon={
                        showWorks ? "mingcute:down-line" : "mingcute:right-line"
                      }
                      width={16}
                    />
                  }
                  sx={{ typography: "s2", fontWeight: 500, px: 0.5 }}
                >
                  What works ({successes.length})
                </Button>
                <Collapse in={showWorks} unmountOnExit>
                  <Stack gap={1.25} sx={{ pt: 1 }}>
                    {successes.map((issue) => (
                      <IssueRow
                        key={`${issue.evalName}-${issue.title}`}
                        issue={issue}
                        first
                        success
                      />
                    ))}
                  </Stack>
                </Collapse>
              </Box>
            )}
          </>
        )}
      </Box>
    </Box>
  );
};

CriticalIssues.propTypes = {
  mode: PropTypes.string,
  evalLinks: PropTypes.object,
};

export default CriticalIssues;
