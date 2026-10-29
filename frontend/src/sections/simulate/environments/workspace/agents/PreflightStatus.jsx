import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { VersionBadge } from "../EnvVersionPin";
import { AGENT_VERSIONS_COPY as COPY } from "../workspace.constants";

const PREFLIGHT_TONES = {
  pending: BUILD_TONES.accent,
  failed: BUILD_TONES.red,
  passed: BUILD_TONES.green,
};
export function PreflightBadge({ status }) {
  const label = COPY.preflight[status];
  if (!label) return null;
  return <VersionBadge label={label} tone={PREFLIGHT_TONES[status]} />;
}
PreflightBadge.propTypes = { status: PropTypes.string };

// The last check: each step's status and the names it found missing.
export function PreflightReport({ report }) {
  if (!report) return null;
  const checks = Array.isArray(report.checks) ? report.checks : [];
  const missing = Array.isArray(report.missing) ? report.missing : [];
  const invalid = Array.isArray(report.invalid) ? report.invalid : [];
  if (!checks.length && !missing.length && !invalid.length && !report.error)
    return null;
  const line = (text, color = "text.secondary") => (
    <Typography sx={{ typography: "s3", color }}>{text}</Typography>
  );
  return (
    <Stack spacing={0.25} sx={{ mt: 0.5 }} data-testid="preflight-report">
      {checks.map((check) => (
        <Typography
          key={check.id || check.label}
          sx={{
            typography: "s3",
            color: check.status === "failed" ? "error.main" : "text.secondary",
          }}
        >
          {`${check.label}: ${COPY.checks[check.status] || check.status}`}
          {check.missing?.length ? ` · ${COPY.missing(check.missing)}` : ""}
        </Typography>
      ))}
      {missing.length > 0 && line(COPY.missing(missing), "error.main")}
      {invalid.length > 0 && line(COPY.invalid(invalid), "error.main")}
      {report.error && line(COPY.checkError(report.error), "error.main")}
    </Stack>
  );
}
PreflightReport.propTypes = { report: PropTypes.object };
