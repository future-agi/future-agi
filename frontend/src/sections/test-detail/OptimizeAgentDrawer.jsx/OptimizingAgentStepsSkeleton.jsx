import React from "react";
import {
  Box,
  Step,
  StepLabel,
  StepContent,
  Stepper,
  Skeleton,
} from "@mui/material";

const OptimizingAgentStepsSkeleton = () => {
  const skeletonSteps = Array.from({ length: 4 });

  return (
    <Stepper
      nonLinear
      orientation="vertical"
      sx={{
        "& .MuiStepConnector-root": {
          marginLeft: "15px",
          marginTop: 0,
          marginBottom: 0,
        },
        "& .MuiStepConnector-line": {
          borderColor: "divider",
          borderLeftWidth: "1px",
          minHeight: "16px",
        },
        "& .MuiStep-root": {
          "& .MuiStepLabel-root": {
            padding: 0,
            alignItems: "flex-start",
          },
        },
      }}
    >
      {skeletonSteps.map((_, index) => (
        <Step key={`skeleton-step-${index}`} expanded>
          <StepLabel
            StepIconComponent={() => (
              <Box
                sx={{
                  width: 30,
                  height: 30,
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  bgcolor: "action.hover",
                  flexShrink: 0,
                }}
              >
                <Skeleton
                  variant="circular"
                  width={14}
                  height={14}
                  sx={{ bgcolor: "text.secondary" }}
                />
              </Box>
            )}
            sx={{
              marginBottom: 0,
              "& .MuiStepLabel-iconContainer": { paddingTop: 0 },
              "& .MuiStepLabel-labelContainer": {
                display: "flex",
                alignItems: "center",
              },
            }}
          >
            <Box display="flex" alignItems="center">
              <Skeleton
                variant="text"
                width={150}
                height={20}
                sx={{ fontSize: "13px" }}
              />
            </Box>
          </StepLabel>

          <StepContent
            sx={{
              marginLeft: "15px",
              borderLeft:
                index === skeletonSteps.length - 1
                  ? "none"
                  : "1px solid var(--border-default)",
              paddingBottom: index === skeletonSteps.length - 1 ? 0 : "16px",
              paddingTop: 0,
            }}
          >
            <Box display="flex" flexDirection="column" gap={1}>
              <Skeleton
                variant="text"
                width="80%"
                height={16}
                sx={{ fontSize: "13px" }}
              />
              <Skeleton
                variant="text"
                width={120}
                height={13}
                sx={{ fontSize: "13px" }}
              />
            </Box>
          </StepContent>
        </Step>
      ))}
    </Stepper>
  );
};

export default OptimizingAgentStepsSkeleton;
