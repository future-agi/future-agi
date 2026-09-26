import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, MobileStepper } from "@mui/material";
import { styled } from "@mui/material/styles";
import SvgColor from "src/components/svg-color";
import { SpaceBackdrop } from "src/components/space-backdrop";
import BlueprintSpaceship from "./BlueprintSpaceship";

// Dots progress indicator — same style as the signup onboarding steps
// (inactive dots + an elongated pill for the active step).
const DotsStepper = styled(MobileStepper)(({ theme }) => ({
  background: "transparent",
  justifyContent: "center",
  padding: 0,
  "& .MuiMobileStepper-dot": {
    width: 12,
    height: 12,
    margin: "0 6px",
    backgroundColor: theme.palette.action.disabled,
    transition: "all 0.3s ease",
  },
  "& .MuiMobileStepper-dotActive": {
    width: 40,
    borderRadius: 8,
    backgroundColor: theme.palette.text.primary,
  },
}));

// Centered shell shared by the OSS first-run steps. A single column on the app
// background with the Future AGI wordmark on top — no promo panel, so the setup
// content sits in the middle of the screen.
export default function OssSetupShell({
  step,
  totalSteps = 2,
  illustration,
  children,
}) {
  return (
    <Box
      sx={{
        width: "100%",
        height: "100vh",
        display: "flex",
        bgcolor: "background.default",
        position: "relative",
        overflowY: "auto",
      }}
    >
      <SpaceBackdrop />

      <Box
        sx={{
          width: "100%",
          maxWidth: 480,
          px: 3,
          py: { xs: 4, md: 4 },
          position: "relative",
          zIndex: 1,
          // margin auto centers vertically + horizontally, staying scrollable
          // if content ever exceeds the viewport.
          m: "auto",
        }}
      >
        <Stack
          direction="row"
          gap={0.75}
          alignItems="center"
          justifyContent="center"
          sx={{ mb: 4 }}
        >
          <SvgColor
            src="/favicon/logo.svg"
            sx={{ height: 34, width: 34, color: "primary.main" }}
          />
          <SvgColor
            src="/logo/future_agi_text.svg"
            sx={{ height: 18, width: 116, color: "primary.main" }}
          />
        </Stack>

        <Stack alignItems="center" sx={{ mb: 3, mt: -1, width: "100%" }}>
          {illustration || <BlueprintSpaceship size={150} />}
        </Stack>

        {typeof step === "number" && (
          <Stack alignItems="center" sx={{ mb: 4 }}>
            <DotsStepper
              variant="dots"
              steps={totalSteps}
              position="static"
              activeStep={step}
            />
          </Stack>
        )}

        {children}
      </Box>
    </Box>
  );
}

OssSetupShell.propTypes = {
  step: PropTypes.number,
  totalSteps: PropTypes.number,
  illustration: PropTypes.node,
  children: PropTypes.node,
};
