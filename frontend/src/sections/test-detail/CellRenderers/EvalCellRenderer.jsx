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
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";

const EvalCellRenderer = ({ value: evalData }) => {
  const isNumeric = evalData?.type === OutputTypes.NUMERIC;

  const canonicalPass =
    typeof evalData?.output_pass === "boolean" ? evalData.output_pass : null;
  const canonicalScore =
    typeof evalData?.output_score === "number" ? evalData.output_score : null;
  const canonicalChoices = Array.isArray(evalData?.output_choices)
    ? evalData.output_choices
    : null;
  const hasCanonical =
    canonicalPass !== null ||
    canonicalScore !== null ||
    canonicalChoices !== null;

  const result = normalizeEvalResult(evalData?.value, evalData?.type);

  const getBgColor = () => {
    if (canonicalPass !== null) {
      return canonicalPass
        ? interpolateColorBasedOnScore(1, 1)
        : interpolateColorBasedOnScore(0, 1);
    }
    if (canonicalScore !== null) {
      const maxScore = canonicalScore <= 1 ? 1 : 100;
      return interpolateColorBasedOnScore(canonicalScore, maxScore);
    }
    if (result.kind === "score") {
      const maxScore = result.score <= 1 ? 1 : 100;
      return interpolateColorBasedOnScore(result.score, maxScore);
    }
    if (result.kind === "passfail") {
      return result.pass
        ? interpolateColorBasedOnScore(1, 1)
        : interpolateColorBasedOnScore(0, 1);
    }
    if (result.kind === "choices" && typeof result.score === "number") {
      const maxScore = result.score <= 1 ? 1 : 100;
      return interpolateColorBasedOnScore(result.score, maxScore);
    }
    return null;
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
    if (isNumeric) {
      return <NumericCell value={evalData?.value} sx={{ padding: "0 12px" }} />;
    }
    if (hasCanonical) {
      // Choices anchor the visible content when present, mirroring the
      // choice-bubble UX the FE PR targets.
      if (canonicalChoices && canonicalChoices.length > 0) {
        return (
          <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
            {canonicalChoices.map((item, idx) => (
              <Chip
                key={`${item}-${idx}`}
                color="primary"
                variant="outlined"
                size="small"
                label={_.capitalize(String(item))}
              />
            ))}
          </Box>
        );
      }
      if (canonicalPass !== null) {
        return canonicalPass ? "Passed" : "Failed";
      }
      if (canonicalScore !== null) {
        const pct = canonicalScore <= 1 ? canonicalScore * 100 : canonicalScore;
        return `${Math.round(pct)}%`;
      }
      return <Box sx={{ padding: 1 }}>-</Box>;
    }
    switch (result.kind) {
      case "score": {
        const pct = result.score <= 1 ? result.score * 100 : result.score;
        return `${Math.round(pct)}%`;
      }
      case "passfail":
        return _.capitalize(result.label);
      case "choices":
        return (
          <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
            {result.items.map((item, idx) => (
              <Chip
                key={`${item}-${idx}`}
                color="primary"
                variant="outlined"
                size="small"
                label={_.capitalize(String(item))}
              />
            ))}
          </Box>
        );
      case "empty":
      default:
        return <Box sx={{ padding: 1 }}>-</Box>;
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
