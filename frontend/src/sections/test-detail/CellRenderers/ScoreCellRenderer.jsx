import React from "react";
import PropTypes from "prop-types";
import { Box, LinearProgress, Skeleton, Typography } from "@mui/material";
import { ShowComponent } from "../../../components/show";
import { getCsatScoreColor } from "src/components/CallLogsDrawer/common";
import { CallExecutionLoadingStatus, TestRunLoadingStatus } from "../common";

const ScoreCellRenderer = ({ value, data }) => {
  const color = getCsatScoreColor(value);
  if (value === null || value === undefined) {
    // Precise signal from BE: only show the loading skeleton while CSAT is
    // actually being computed. Eval-only reruns don't recompute CSAT, so the
    // BE doesn't transition this back to "running" — the cell stays as "-".
    const callMetadata = data?.callMetadata ?? data?.call_metadata;
    const csatStatus = callMetadata?.csatStatus ?? callMetadata?.csat_status;
    if (csatStatus === "running") {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            height: "100%",
            width: "100%",
          }}
        >
          <Skeleton sx={{ width: "100%", height: "20px" }} variant="rounded" />
        </Box>
      );
    }
    // Fallback for older rows persisted before csat_status was tracked:
    // infer from the broader call/run loading state.
    if (csatStatus === undefined) {
      const callLoading = CallExecutionLoadingStatus.includes(
        data?.status?.toLowerCase?.(),
      );
      const runLoading = TestRunLoadingStatus.includes(
        data?.overall_status?.toLowerCase?.(),
      );
      if (callLoading || runLoading) {
        return (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              height: "100%",
              width: "100%",
            }}
          >
            <Skeleton
              sx={{ width: "100%", height: "20px" }}
              variant="rounded"
            />
          </Box>
        );
      }
    }
    return <Typography typography="s1">- </Typography>;
  }
  return (
    <Box sx={{ display: "flex", alignItems: "center", width: "100%", gap: 2 }}>
      <Typography
        typography="s1"
        fontWeight={"fontWeightBold"}
        color={value !== null ? color : null}
      >
        {value === null ? "Score not available" : `${value}`}
      </Typography>
      <ShowComponent condition={value !== null}>
        <LinearProgress
          value={(value / 10) * 100}
          variant="determinate"
          sx={{
            width: "30%",
            height: "10px",
            "& .MuiLinearProgress-bar": {
              backgroundColor: color,
            },
          }}
        />
      </ShowComponent>
    </Box>
  );
};

ScoreCellRenderer.propTypes = {
  value: PropTypes.number,
  data: PropTypes.object,
};

export default ScoreCellRenderer;
