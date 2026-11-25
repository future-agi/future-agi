import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { BUILD_TONES } from "../../buildEnvironment/buildTones";

import { checkIcon } from "./fixRunView";

// One check of a verdict: its label, status and detail (see describeChecks). A check
// that ran but could not measure reads "Couldn't measure", with its own icon; one
// skipped because an earlier check failed (`notRun`) says which check that was.

export default function CheckLine({
  check,
  variant = "s3",
  nameWidth,
  noWrap = false,
  sx,
}) {
  return (
    <Stack
      data-check={
        check.status === "skipped"
          ? check.notRun
            ? "not-run"
            : "unmeasured"
          : check.status
      }
      direction="row"
      spacing={variant === "s2" ? 0.75 : 1}
      alignItems="center"
      sx={{ minWidth: 0, ...sx }}
    >
      <Iconify
        icon={checkIcon(check)}
        width={14}
        sx={{
          color: check.status === "failed" ? BUILD_TONES.red : "text.secondary",
          flexShrink: 0,
        }}
      />
      <Typography
        noWrap={noWrap}
        sx={{
          typography: variant,
          fontWeight: 600,
          flexShrink: 0,
          ...(nameWidth && { width: nameWidth }),
        }}
      >
        {check.label || check.name}
      </Typography>
      {check.status === "skipped" && (
        <Typography
          noWrap
          sx={{
            typography: variant,
            fontWeight: 600,
            color: "text.secondary",
            flexShrink: 0,
          }}
        >
          {check.notRun
            ? `not run — an earlier check failed: ${check.notRunBecause}`
            : check.unknown || "Couldn't measure"}
        </Typography>
      )}
      {/* A check that was not run has nothing to add beyond saying so. */}
      {!check.notRun && (
        <Typography
          noWrap={noWrap}
          sx={{ typography: variant, color: "text.secondary", minWidth: 0 }}
        >
          {check.detail}
        </Typography>
      )}
    </Stack>
  );
}

CheckLine.propTypes = {
  check: PropTypes.object.isRequired,
  variant: PropTypes.oneOf(["s2", "s3"]),
  nameWidth: PropTypes.number,
  noWrap: PropTypes.bool,
  sx: PropTypes.object,
};
