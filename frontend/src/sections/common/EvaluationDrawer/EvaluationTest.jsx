import React, { useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Typography,
  IconButton,
  Chip,
  Divider,
  Skeleton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { interpolateColorforExperiment } from "src/sections/experiment-detail/ExperimentData/Common";
import CellMarkdown from "../CellMarkdown";

const chipColor = (status) => {
  switch (status) {
    case "Passed":
      return {
        color: "green.500",
        backgroundColor: "green.o10",
      };
    case "Failed":
      return {
        color: "red.500",
        backgroundColor: "red.o10",
      };
    default:
      return {
        color: "text.primary",
        backgroundColor: "action.disabledBackground",
      };
  }
};

const EvaluationTest = ({ onClose, testingEvalData, testingEvalLoading }) => {
  const testResults = useMemo(() => {
    const responses = testingEvalData?.data?.result?.responses || [];

    return responses.map((res, index) => {
      const { outputType, output, reason } = res;
      const row = index + 1;

      let chip = null;

      if (outputType === "Pass/Fail") {
        chip = {
          label: output,
          sx: chipColor(output),
        };
      }

      if (outputType === "score" && typeof output === "number") {
        const percent = Math.round(output * 100);
        const factor = percent; // Since factor is based on percent (0–100)

        let textColor = "text.primary";
        if (factor <= 49) {
          textColor = "red.500";
        } else if (factor <= 79) {
          textColor = "orange.500";
        } else {
          textColor = "green.500";
        }

        chip = {
          label: `${percent}%`,
          sx: {
            backgroundColor: interpolateColorforExperiment(output * 10),
            color: textColor,
          },
        };
      }

      return {
        row,
        chip,
        message:
          reason ||
          (outputType === "Pass/Fail"
            ? output === "Passed"
              ? "All checks passed successfully."
              : "Evaluation failed."
            : "Evaluation failed without a specific reason."),
      };
    });
  }, [testingEvalData]);

  return (
    <Box
      sx={{
        width: "37.5vw",
        paddingRight: 2,
        borderRight: "1px solid",
        borderColor: "divider",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
        overflowY: "auto",
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
        }}
      >
        <Box>
          <Typography fontWeight={600} fontSize={16} color={"text.primary"}>
            Test Eval
          </Typography>
          <Typography fontWeight={400} fontSize={14} color={"text.secondary"}>
            Test runs on the first 3 rows of a dataset
          </Typography>
        </Box>
        <IconButton onClick={onClose} sx={{ p: 0, color: "text.primary" }}>
          <Iconify icon="line-md:close" width={24} height={24} />
        </IconButton>
      </Box>

      {/* Content */}
      {testingEvalLoading
        ? Array.from({ length: 3 }).map((_, i) => (
            <Box
              key={i}
              sx={{
                width: "100%",
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                height: "120px",
                overflow: "hidden",
                padding: 2,
                display: "flex",
                flexDirection: "column",
                gap: 1,
              }}
            >
              <Skeleton width="40%" height={20} />
              <Skeleton variant="rounded" width="30%" height={24} />
              <Skeleton width="100%" height={20} />
              <Skeleton width="90%" height={20} />
            </Box>
          ))
        : testResults.map(({ row, chip, message }) => (
            <Box
              key={row}
              sx={{
                width: "100%",
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  backgroundColor: "background.paper",
                  paddingX: 2,
                  paddingY: 1,
                }}
              >
                <Typography
                  fontWeight={500}
                  fontSize={14}
                  color={"text.primary"}
                >
                  Result - row {row}
                </Typography>

                {chip && (
                  <Chip
                    label={chip.label}
                    variant="soft"
                    size="small"
                    sx={{
                      pointerEvents: "none",
                      fontSize: "12px",
                      fontWeight: 400,
                      ...chip.sx,
                    }}
                  />
                )}
              </Box>

              <Divider />

              <Box sx={{ padding: 2 }}>
                <Typography variant="body2" sx={{ cursor: "default" }}>
                  <CellMarkdown spacing={0} text={message} />
                </Typography>
              </Box>
            </Box>
          ))}
    </Box>
  );
};

EvaluationTest.propTypes = {
  onClose: PropTypes.func,
  testingEvalData: PropTypes.object,
  testingEvalLoading: PropTypes.bool,
};

export default EvaluationTest;
