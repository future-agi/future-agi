import React from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  CircularProgress,
  Step,
  StepLabel,
  StepContent,
  Stepper,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import { format, isValid } from "date-fns";
import Iconify from "src/components/iconify";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// Status constants
const AgentPromptOptimizerStatus = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
};

const AgentPromptOptimizerRefetchStates = [
  AgentPromptOptimizerStatus.PENDING,
  AgentPromptOptimizerStatus.RUNNING,
];

// Custom step icon component
const STEP_STATUS_CONFIG = {
  completed: {
    bgColor: "green.o10",
    iconType: "svg",
    iconColor: "green.500",
    src: "/assets/icons/ic_check_with_circle_tick.svg",
  },
  running: {
    bgColor: "blue.o5",
    iconType: "svg",
    iconColor: "blue.500",
    src: "/assets/icons/ic_check_with_circle_tick.svg",
  },
  failed: {
    bgColor: "red.o10",
    iconType: "iconify",
    icon: "mdi:close",
    iconColor: "red.500",
  },
  pending: {
    bgColor: "action.hover",
    iconType: "svg",
    src: "/assets/icons/ic_check_with_circle_tick.svg",
    iconColor: "text.disabled",
  },
};

const CustomStepIcon = ({ step, isFailedStep }) => {
  const { status } = step;
  const effectiveStatus =
    status === "failed" || isFailedStep ? "failed" : status;
  const config =
    STEP_STATUS_CONFIG[effectiveStatus] || STEP_STATUS_CONFIG.pending;
  const isRunning = effectiveStatus === AgentPromptOptimizerStatus.RUNNING;

  return (
    <Box
      sx={{
        width: 30,
        height: 30,
        borderRadius: "50%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: config.bgColor,
      }}
    >
      {isRunning ? (
        <CircularProgress
          size={14}
          thickness={5}
          sx={{ color: config.iconColor }}
          aria-label="Step in progress"
        />
      ) : config.iconType === "svg" ? (
        <SvgColor
          sx={{ height: "14px", width: "14px", bgcolor: config.iconColor }}
          src={config.src}
        />
      ) : (
        <Iconify
          icon={config.icon}
          width={14}
          sx={{ color: config.iconColor }}
        />
      )}
    </Box>
  );
};

CustomStepIcon.propTypes = {
  step: PropTypes.object,
  isFailedStep: PropTypes.bool,
};

/**
 * Dataset Optimization Steps Component
 *
 * Similar to OptimizingAgentSteps from simulation, but uses dataset optimization endpoints.
 */
const DatasetOptimizingSteps = ({ status, optimizationId }) => {
  const { data: steps, isPending: isPendingSteps } = useQuery({
    queryKey: ["dataset-optimization-steps", optimizationId],
    queryFn: () =>
      axios.get(endpoints.develop.datasetOptimization.steps(optimizationId)),
    enabled: !!optimizationId,
    refetchInterval: () => {
      if (AgentPromptOptimizerRefetchStates.includes(status)) {
        return 5000;
      }
      return false;
    },
    select: (data) => data?.data?.result,
  });

  const stepsArray = Array.isArray(steps) ? steps : [];

  return (
    <Accordion
      defaultExpanded={status !== AgentPromptOptimizerStatus.COMPLETED}
      disableGutters
      sx={{
        border: "1px solid var(--border-default)",
        borderRadius: "4px !important",
        boxShadow: "none",
        "&:before": { display: "none" },
        "&.Mui-expanded": {
          margin: 0,
        },
      }}
    >
      <AccordionSummary
        expandIcon={
          <Iconify
            icon="line-md:chevron-up"
            width={22}
            height={22}
            color="text.primary"
          />
        }
        sx={{
          px: 2,
          py: 1.5,
          minHeight: "auto !important",
          "& .MuiAccordionSummary-content": {
            margin: 0,
          },
          "& .MuiAccordionSummary-expandIconWrapper": {
            transform: "rotate(-180deg)",
            transition: "transform 0.2s",
          },
          "& .MuiAccordionSummary-expandIconWrapper.Mui-expanded": {
            transform: "rotate(0deg)",
          },
        }}
      >
        <Box display="flex" alignItems="center" gap={1}>
          <SvgColor
            sx={{ width: 16 }}
            src="/assets/icons/navbar/ic_optimize.svg"
          />
          <Typography variant="body1" fontWeight={500} fontSize="14px">
            Optimization Steps
          </Typography>
        </Box>
      </AccordionSummary>

      <AccordionDetails
        sx={{ px: 2, py: 2, borderTop: "1px solid var(--border-default)" }}
      >
        {isPendingSteps ? (
          <Box sx={{ p: 2 }}>
            <Typography color="text.secondary">Loading steps...</Typography>
          </Box>
        ) : (
          <Stepper
            nonLinear
            activeStep={stepsArray.length || 0}
            orientation="vertical"
            sx={{
              "& .MuiStepConnector-root": {
                marginLeft: "14px",
                marginTop: 0,
                marginBottom: 0,
              },
              "& .MuiStepConnector-line": {
                borderColor: "divider",
                borderLeftWidth: "1px",
                minHeight: "18px",
              },
              "& .MuiStep-root": {
                "& .MuiStepLabel-root": {
                  padding: 0,
                  alignItems: "flex-start",
                },
              },
            }}
          >
            {stepsArray.map((step, index) => {
              const isFailedStep =
                stepsArray
                  .slice(0, index)
                  .some((s) => s?.status === "failed") ||
                step?.status === "failed";

              return (
                <Step
                  key={step.id || index}
                  completed={step.status === "completed"}
                  expanded
                >
                  <StepLabel
                    StepIconComponent={() => (
                      <CustomStepIcon step={step} isFailedStep={isFailedStep} />
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
                    <Box display="flex" alignItems="center" gap={0}>
                      <Typography
                        variant="s1"
                        fontWeight="fontWeightMedium"
                        color={
                          step.status === AgentPromptOptimizerStatus.PENDING
                            ? "text.secondary"
                            : "text.primary"
                        }
                      >
                        {step.name}
                      </Typography>
                    </Box>
                  </StepLabel>

                  <StepContent
                    sx={{
                      marginLeft: "14px",
                      borderLeft:
                        index === stepsArray.length - 1
                          ? "none"
                          : "1px solid #E5E7EB",
                      borderImage:
                        index === stepsArray.length - 1
                          ? "none"
                          : "linear-gradient(to bottom, transparent 6px, #E5E7EB 6px) 1",
                      paddingBottom:
                        index === stepsArray.length - 1 ? 0 : "16px",
                      paddingTop: 0,
                      marginTop: "-6px",
                      paddingLeft: "22px",
                    }}
                  >
                    <ShowComponent
                      condition={
                        Boolean(step.description) &&
                        [
                          AgentPromptOptimizerStatus.RUNNING,
                          AgentPromptOptimizerStatus.COMPLETED,
                          AgentPromptOptimizerStatus.FAILED,
                        ].includes(step.status)
                      }
                    >
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        fontSize="14px"
                      >
                        {step.description}
                      </Typography>
                    </ShowComponent>

                    <ShowComponent
                      condition={
                        Boolean(step.updated_at) &&
                        isValid(new Date(step.updated_at)) &&
                        [
                          AgentPromptOptimizerStatus.COMPLETED,
                          AgentPromptOptimizerStatus.FAILED,
                        ].includes(step.status)
                      }
                    >
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        fontSize="13px"
                      >
                        {step.updated_at && isValid(new Date(step.updated_at))
                          ? format(
                              new Date(step.updated_at),
                              "dd/MM/yyyy,HH:mm:ss",
                            )
                          : ""}
                      </Typography>
                    </ShowComponent>
                  </StepContent>
                </Step>
              );
            })}
          </Stepper>
        )}
      </AccordionDetails>
    </Accordion>
  );
};

DatasetOptimizingSteps.propTypes = {
  status: PropTypes.string,
  optimizationId: PropTypes.string,
};

export default DatasetOptimizingSteps;
