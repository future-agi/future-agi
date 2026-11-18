import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";

const StepsTracker = ({
  steps = [],
  currentStep = 0,
  setCurrentStep = () => {},
  validatedSteps = [],
}) => {
  return (
    <Stack
      direction="row"
      alignItems="center"
      sx={{
        flexWrap: "wrap",
        backgroundColor: "background.paper",
        borderRadius: 2,
        gap: 3,
      }}
    >
      {steps.map((section, index) => {
        const isActive = currentStep === index;
        const isValidated = validatedSteps[index];

        return (
          <Box
            key={section.id}
            display="flex"
            alignItems="center"
            gap={2}
            sx={{
              flexShrink: 0,
              cursor:
                isValidated || index === currentStep ? "pointer" : "default",
              opacity: isValidated || index === currentStep ? 1 : 0.5,
            }}
            onClick={() => {
              if (isValidated || index === currentStep) setCurrentStep(index);
            }}
          >
            {/* Step Number or Check */}
            <Box
              sx={{
                width: 32,
                height: 32,
                borderRadius: "50%",
                border: isValidated ? "none" : "1px solid",
                borderColor: isActive
                  ? "text.primary"
                  : "text.disabled !important",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontWeight: "fontWeightMedium",
                transition: "all 0.2s ease",
                backgroundColor: isValidated
                  ? "status.success.solid"
                  : "transparent",
              }}
            >
              {isValidated ? (
                <SvgColor
                  src="/assets/icons/ic_check.svg"
                  sx={{ width: 24, height: 24, color: "common.white" }}
                />
              ) : (
                index + 1
              )}
            </Box>

            {/* Step Title */}
            <Typography
              typography="m3"
              fontWeight="fontWeightMedium"
              color={
                isActive || isValidated
                  ? "text.primary"
                  : "text.disabled !important"
              }
            >
              {section.title}
            </Typography>

            {/* Connector */}
            {index < steps.length - 1 && (
              <SvgColor
                src="/assets/icons/navbar/ic_nav_close_toggle.svg"
                sx={{
                  height: 25,
                  width: 25,
                  transition: "opacity 0.2s ease",
                  opacity:
                    currentStep === index || validatedSteps[index] ? 1 : 0.3,
                }}
              />
            )}
          </Box>
        );
      })}
    </Stack>
  );
};

export default StepsTracker;

StepsTracker.propTypes = {
  steps: PropTypes.array.isRequired,
  currentStep: PropTypes.number.isRequired,
  setCurrentStep: PropTypes.func.isRequired,
  validatedSteps: PropTypes.array.isRequired,
};
