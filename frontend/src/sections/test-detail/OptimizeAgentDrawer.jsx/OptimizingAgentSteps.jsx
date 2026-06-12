import React from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
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
import {
  AgentPromptOptimizerRefetchStates,
  AgentPromptOptimizerStatus,
} from "../FixMyAgentDrawer/common";
import OptimizingAgentStepsSkeleton from "./OptimizingAgentStepsSkeleton";
import CustomStepIcon from "./CustomIcon";

const OptimizingAgentSteps = ({ status, optimizationId }) => {
  const { data: steps, isPending: isPendingSteps } = useQuery({
    queryKey: ["fix-my-agent-optimization-steps", optimizationId],
    queryFn: () =>
      axios.get(
        endpoints.optimizeSimulate.getOptimizationSteps(optimizationId),
      ),
    enabled: !!optimizationId,
    refetchInterval: ({ state: _state }) => {
      if (AgentPromptOptimizerRefetchStates.includes(status)) {
        return 5000;
      }
      return false;
    },
    select: (data) => data?.data?.result,
  });

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
          <OptimizingAgentStepsSkeleton />
        ) : (
          <Stepper
            nonLinear
            activeStep={steps?.length || 0}
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
            {steps?.map(({ status, name, description, updatedAt }, index) => {
              const isFailedStep =
                steps
                  .slice(0, index)
                  .some((step) => step?.status === "failed") || //check if any previous step has failed or current step is failed then render failed status
                status === "failed";

              return (
                <Step key={index} completed={status === "completed"} expanded>
                  <StepLabel
                    StepIconComponent={() => (
                      <CustomStepIcon
                        step={{ status, name, description, updatedAt }}
                        isFailedStep={isFailedStep}
                      />
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
                      <Typography variant="s1" fontWeight={"fontWeightMedium"}>
                        {name}
                      </Typography>
                    </Box>
                  </StepLabel>

                  <StepContent
                    sx={{
                      marginLeft: "15px",
                      borderLeft:
                        index === steps.length - 1
                          ? "none"
                          : "1px solid var(--border-default)",
                      borderImage:
                        index === steps.length - 1
                          ? "none"
                          : "linear-gradient(to bottom, transparent 6px, var(--border-default) 6px) 1",
                      paddingBottom: index === steps.length - 1 ? 0 : "16px",
                      paddingTop: 0,
                      marginTop: "-6px",
                      paddingLeft: "22px",
                    }}
                  >
                    <ShowComponent
                      condition={
                        Boolean(description) &&
                        [
                          AgentPromptOptimizerStatus.RUNNING,
                          AgentPromptOptimizerStatus.COMPLETED,
                          AgentPromptOptimizerStatus.FAILED,
                        ].includes(status)
                      }
                    >
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        fontSize="14px"
                      >
                        {description}
                      </Typography>
                    </ShowComponent>

                    <ShowComponent
                      condition={
                        Boolean(updatedAt) &&
                        isValid(new Date(updatedAt)) &&
                        [
                          AgentPromptOptimizerStatus.COMPLETED,
                          AgentPromptOptimizerStatus.FAILED,
                        ].includes(status)
                      }
                    >
                      <Typography
                        variant="caption"
                        color="text.disabled"
                        fontSize="13px"
                      >
                        {format(new Date(updatedAt), "dd/MM/yyyy,HH:mm:ss")}
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

OptimizingAgentSteps.propTypes = {
  status: PropTypes.string,
  optimizationId: PropTypes.string,
};

export default OptimizingAgentSteps;
