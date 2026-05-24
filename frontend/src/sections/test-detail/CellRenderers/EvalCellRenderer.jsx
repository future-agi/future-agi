import React from "react";
import { Box, Chip, Skeleton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import _ from "lodash";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import FormattedValueReason from "src/sections/evals/EvaluationsTabs/FormattedReason";
import { CallExecutionLoadingStatus, TestRunLoadingStatus } from "../common";
import NumericCell from "../../common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

const getScorePercentage = (s, decimalPlaces = 0) => {
  if (s <= 0) s = 0;
  // Scores on 0-1 scale get multiplied by 100; scores already on 0-100 stay as-is
  const score = s <= 1 ? s * 100 : s;
  return Number(score.toFixed(decimalPlaces));
};

const EvalCellRenderer = ({ value: evalData }) => {
  const getBgColor = () => {
    if (evalData?.type === "score") {
      if (!_.isNumber(evalData?.value)) return "";
      const maxScore = evalData.value <= 1 ? 1 : 100;
      return interpolateColorBasedOnScore(evalData.value, maxScore);
    } else if (evalData?.type === "Pass/Fail") {
      return evalData?.value
        ? evalData?.value?.includes("Fail")
          ? interpolateColorBasedOnScore(0, 1)
          : interpolateColorBasedOnScore(1, 1)
        : "";
    } else if (
      evalData?.type === "choices" ||
      evalData?.type === OutputTypes.NUMERIC
    ) {
      return null;
    }
  };

  const renderContent = () => {
    const {
      overall_status: _overallStatus,
      call_status: _callStatus,
      ...restEvalData
    } = evalData || {};
    const hasEvalData = Object.keys(restEvalData).length > 0;
    const callLoading = CallExecutionLoadingStatus.includes(
      evalData?.call_status?.toLowerCase(),
    );
    const runLoading = TestRunLoadingStatus.includes(
      evalData?.overall_status?.toLowerCase(),
    );
    if (!hasEvalData && (callLoading || runLoading))
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
    if (evalData?.error) {
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
    if (
      evalData?.value === null ||
      evalData?.value === undefined ||
      (Array.isArray(evalData?.value) && evalData.value.length === 0)
    ) {
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
        <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
          {evalData?.value?.map((item, idx) => {
            const label =
              typeof item === "object"
                ? item?.choice ?? item?.value ?? JSON.stringify(item)
                : item;
            return (
              <Chip
                color="primary"
                variant="outlined"
                size="small"
                key={label || idx}
                label={_.capitalize(String(label))}
              />
            );
          })}
        </Box>
      );
    } else if (evalData?.type === OutputTypes.NUMERIC) {
      return <NumericCell value={evalData?.value} sx={{ padding: "0 12px" }} />;
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

EvalCellRenderer.propTypes = {
  data: PropTypes.object,
  value: PropTypes.any,
  column: PropTypes.object,
};

export default EvalCellRenderer;
