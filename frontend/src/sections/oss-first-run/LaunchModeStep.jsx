import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, Divider } from "@mui/material";
import { alpha } from "@mui/material/styles";
import LoadingButton from "@mui/lab/LoadingButton";
import Iconify from "src/components/iconify";
import { LAUNCH_MODES, MODE_NOTE } from "./constants";

export default function LaunchModeStep({ value, onChange, onContinue }) {
  const renderHead = (
    <Stack sx={{ mb: 4 }}>
      <Typography
        variant="l2"
        component="h1"
        fontWeight="fontWeightSemiBold"
        sx={{ color: "text.primary" }}
      >
        Select a launch mode
      </Typography>
      <Typography
        variant="s1_2"
        sx={{ color: "text.secondary", maxWidth: 440, mt: 1 }}
      >
        What&apos;s your plan for this installation? We&apos;ll run the
        infrastructure checks accordingly.
      </Typography>
    </Stack>
  );

  const renderOptions = (
    <Stack spacing={1.5} sx={{ maxWidth: 440 }}>
      {LAUNCH_MODES.map((mode) => {
        const selected = value === mode.id;
        return (
          <Box
            key={mode.id}
            role="button"
            tabIndex={0}
            aria-pressed={selected}
            onClick={() => onChange(mode.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onChange(mode.id);
              }
            }}
            sx={{
              cursor: "pointer",
              borderRadius: 1,
              border: "1px solid",
              borderColor: selected ? "primary.main" : "divider",
              bgcolor: (theme) =>
                selected
                  ? alpha(theme.palette.primary.main, 0.12)
                  : "background.paper",
              p: 2,
              transition: "border-color 0.2s ease, background-color 0.2s ease",
              "&:hover": {
                borderColor: selected ? "primary.main" : "text.disabled",
              },
            }}
          >
            <Stack direction="row" spacing={1.5} alignItems="flex-start">
              <Box
                sx={{
                  width: 36,
                  height: 36,
                  borderRadius: 1,
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  bgcolor: selected ? "primary.main" : "action.hover",
                  color: selected ? "primary.contrastText" : "text.secondary",
                }}
              >
                <Iconify icon={mode.icon} width={20} />
              </Box>
              <Stack sx={{ flex: 1 }}>
                <Stack
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                >
                  <Typography
                    variant="s1_2"
                    fontWeight="fontWeightSemiBold"
                    sx={{ color: "text.primary" }}
                  >
                    {mode.title}
                  </Typography>
                  <Iconify
                    icon={
                      selected
                        ? "solar:check-circle-bold"
                        : "solar:record-linear"
                    }
                    width={20}
                    sx={{ color: selected ? "primary.main" : "text.disabled" }}
                  />
                </Stack>
                <Typography
                  variant="s2_1"
                  sx={{ color: "text.secondary", mt: 0.5 }}
                >
                  {mode.description}
                </Typography>
              </Stack>
            </Stack>
          </Box>
        );
      })}

      <Divider sx={{ borderStyle: "dashed", my: 1 }} />

      <Typography
        variant="s2_1"
        sx={{ color: "text.secondary", textAlign: "center" }}
      >
        {MODE_NOTE[value]}
      </Typography>

      <LoadingButton
        fullWidth
        color="primary"
        variant="contained"
        onClick={onContinue}
        disabled={!value}
        sx={{ height: 42, borderRadius: 0.5, mt: 1 }}
      >
        Continue
      </LoadingButton>
    </Stack>
  );

  return (
    <>
      {renderHead}
      {renderOptions}
    </>
  );
}

LaunchModeStep.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  onContinue: PropTypes.func.isRequired,
};
