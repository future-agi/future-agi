import { Box, Chip, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { statusColors } from "src/utils/runStatus";
import { ShowComponent } from "src/components/show";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

const getColorByValues = (data, type, theme) => {
  const tone = (role) => {
    const c = statusColors(theme, role);
    return { color: c.fg, backgroundColor: c.bg };
  };

  let colors = {
    backgroundColor: "",
    color: "",
  };

  if (type == "bool") {
    const isPass = data?.avgBoolPassScore;
    colors = {
      ...colors,
      ...tone(isPass ? "success" : "danger"),
    };
  } else if (type == "float") {
    if (
      (data.avgFloatScore >= 0 && data.avgFloatScore <= 49) ||
      !data.avgFloatScore
    ) {
      colors = {
        ...colors,
        ...tone("danger"),
      };
    } else if (data.avgFloatScore >= 50 && data.avgFloatScore <= 79) {
      colors = {
        ...colors,
        ...tone("warning"),
      };
    } else if (data.avgFloatScore >= 80) {
      colors = {
        ...colors,
        ...tone("success"),
      };
    }
  } else if (type === OutputTypes.NUMERIC) {
    colors = {
      color: "",
      backgroundColor: "",
    };
  } else {
    colors = {
      ...colors,
      color: theme.palette.primary.main,
      backgroundColor: theme.palette.action.hover,
    };
  }

  return colors;
};

const getChipLabel = (data, type) => {
  if (type === "bool") {
    if (data?.avgBoolPassScore) {
      return `Average Pass: ${data.avgBoolPassScore}%`;
    } else if (data?.avgBoolFailScore) {
      return `Average Fail: ${data.avgBoolFailScore}%`;
    } else {
      return "Average Pass: NA";
    }
  } else if (type === "float") {
    const score = parseFloat(data?.avgFloatScore);
    if (typeof score === "number" && !isNaN(score)) {
      return `Average Score: ${score === 0 ? "0" : Number(score.toFixed(2))}%`;
    } else {
      return "Average Score: NA";
    }
  } else if (type === OutputTypes.NUMERIC) {
    const numValue = Number(data?.avgFloatScore);
    const safeValue = isNaN(numValue)
      ? "-"
      : Number.isInteger(numValue)
        ? String(numValue)
        : numValue.toFixed(4);
    return `Average Score: ${safeValue}`;
  } else {
    return null;
  }
};

function evaluateResult(data, evalId) {
  const evalType = data?.evalType;
  const totalErrors = data?.totalErrorsCount || 0;

  const result = {
    ...data,
    evaluationId: evalId,
    errorType: "SUCCESS",
    errorTitle: "Evaluation Success",
  };

  if (evalType === "bool") {
    const hasPassScore = data.avgBoolPassScore !== null;
    const hasFailScore = data.avgBoolFailScore !== null;

    if ((hasPassScore || hasFailScore) && totalErrors > 0) {
      result.errorType = "PARTIAL";
      result.errorTitle = "Evaluation Failed Partially";
      result.showIcon = true;
    }
    if (!(hasPassScore || hasFailScore) && totalErrors === 0) {
      result.errorType = "ERROR";
      result.errorTitle = "Evaluation Failed";
      result.showIcon = true;
    }
    if (!(hasPassScore || hasFailScore) && totalErrors > 0) {
      result.errorType = "ERROR";
      result.errorTitle = "Evaluation Failed";
      result.showIcon = true;
    }
  } else if (evalType === "float") {
    const hasScore = data.avgFloatScore !== null;

    if (hasScore && totalErrors > 0) {
      result.errorType = "PARTIAL";
      result.errorTitle = "Evaluation Failed Partially";
      result.showIcon = true;
    }

    if (!hasScore && totalErrors === 0) {
      result.errorType = "ERROR";
      result.errorTitle = "Evaluation Failed";
      result.showIcon = true;
    }

    if (!hasScore && totalErrors > 0) {
      result.errorType = "ERROR";
      result.errorTitle = "Evaluation Failed";
      result.showIcon = true;
    }
  }

  return result;
}

const CustomChipRenderer = ({ data, evalId, type, handleErrorClick }) => {
  const theme = useTheme();
  const chipColor = getColorByValues(data, type, theme);
  const errorData = evaluateResult(data, evalId);
  return (
    <>
      <ShowComponent condition={type == "bool"}>
        <Box
          onClick={() => handleErrorClick(errorData)}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: (theme) => theme.spacing(1.5),
          }}
        >
          <Chip
            label={getChipLabel(data, type)}
            variant="soft"
            sx={{
              color: chipColor?.color,
              background: chipColor?.backgroundColor,
              transition: "none",
              cursor: errorData?.showIcon ? "pointer" : "default",
              "&:hover": {
                background: chipColor?.backgroundColor,
                color: chipColor?.color,
              },
            }}
          />
          {errorData?.showIcon && (
            <Iconify
              icon="solar:danger-triangle-linear"
              cursor={errorData?.showIcon ? "pointer" : "default"}
              color={
                errorData?.errorType == "PARTIAL"
                  ? "status.warning.fg"
                  : "status.danger.fg"
              }
            />
          )}
        </Box>
      </ShowComponent>
      <ShowComponent condition={type == "float"}>
        <Box
          onClick={() => handleErrorClick(errorData)}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: (theme) => theme.spacing(1.5),
          }}
        >
          <Chip
            label={getChipLabel(data, type)}
            variant="soft"
            sx={{
              color: chipColor?.color,
              background: chipColor?.backgroundColor,
              transition: "none",
              cursor: errorData?.showIcon ? "pointer" : "default",
              "&:hover": {
                background: chipColor?.backgroundColor,
                color: chipColor?.color,
              },
            }}
          />
          {errorData?.showIcon && (
            <Iconify
              icon="solar:danger-triangle-linear"
              cursor={errorData?.showIcon ? "pointer" : "default"}
              color={
                errorData?.errorType == "PARTIAL"
                  ? "status.warning.fg"
                  : "status.danger.fg"
              }
            />
          )}
        </Box>
      </ShowComponent>
      <ShowComponent condition={type == "str_list"}>
        {data?.strListScore &&
          Object?.entries(data?.strListScore).map((item, index) => (
            <Chip
              key={index}
              label={`${item[0]}: ${item[1]?.score}%`}
              variant="soft"
              sx={{
                color: chipColor?.color,
                background: chipColor?.backgroundColor,
                transition: "none",
                cursor: "default",
                "&:hover": {
                  background: chipColor?.backgroundColor,
                  color: chipColor?.color,
                },
              }}
            />
          ))}
      </ShowComponent>
    </>
  );
};

CustomChipRenderer.propTypes = {
  evalId: PropTypes.string,
  data: PropTypes.object,
  type: PropTypes.string,
  handleErrorClick: PropTypes.func,
};

export default CustomChipRenderer;
