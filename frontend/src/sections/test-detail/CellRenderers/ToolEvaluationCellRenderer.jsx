import React from "react";
import { Box, Skeleton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import _ from "lodash";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import FormattedValueReason from "src/sections/evals/EvaluationsTabs/FormattedReason";
import { ToolEvalStatus } from "../common";

const getScorePercentage = (s, decimalPlaces = 0) => {
  if (s <= 0) s = 0;
  // Scores on 0-1 scale get multiplied by 100; scores already on 0-100 stay as-is
  const score = s <= 1 ? s * 100 : s;
  return Number(score.toFixed(decimalPlaces));
};

const ToolEvaluationCellRenderer = ({ value: evalData }) => {
  const getBgColor = () => {
    if (evalData?.type === "score") {
      return _.isNumber(evalData?.value)
        ? interpolateColorBasedOnScore(evalData?.value, 1)
        : "";
    } else if (evalData?.type === "Pass/Fail") {
      return evalData?.value
        ? evalData?.value === "Failed"
          ? interpolateColorBasedOnScore(0, 1)
          : interpolateColorBasedOnScore(1, 1)
        : "";
    } else if (evalData?.type === "choices") {
      return null;
    }
  };

  const renderContent = () => {
    if (evalData?.status === ToolEvalStatus.RUNNING)
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
    if (evalData?.status === ToolEvalStatus.FAILED || evalData?.error) {
      return (
        <Box
          sx={{
            color: "error.main",
            opacity: 1,
            flex: 1,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <Typography variant="body2" align="center">
            Error
          </Typography>
        </Box>
      );
    }
    if (Object.keys(evalData || {}).length === 0) {
      return <Box sx={{ padding: 1 }}>-</Box>;
    }
    if (evalData?.type === "score") {
      return _.isNumber(evalData?.value)
        ? `${getScorePercentage(evalData?.value)}%`
        : "";
    } else if (evalData?.type === "Pass/Fail") {
      return _.capitalize(evalData?.value);
    } else if (evalData?.type === "choices") {
      return (
        <Typography variant="body2" color="text.primary">
          {evalData?.value?.map((item) => _.capitalize(item)).join(" · ")}
        </Typography>
      );
    }
  };

  return (
    <CustomTooltip
      show={evalData?.reason?.length}
      placement="bottom"
      title={FormattedValueReason(evalData?.reason)}
      arrow
      size="small"
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          height: "100%",
          width: "100%",
          flex: 1,
          padding: "4px 8px",
          color: "text.primary",
          backgroundColor: getBgColor(),
        }}
      >
        {renderContent()}
      </Box>
    </CustomTooltip>
  );
};

ToolEvaluationCellRenderer.propTypes = {
  data: PropTypes.object,
  value: PropTypes.any,
  column: PropTypes.object,
};

export default ToolEvaluationCellRenderer;
