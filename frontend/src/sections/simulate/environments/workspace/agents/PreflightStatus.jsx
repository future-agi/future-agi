import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { VersionBadge } from "../EnvVersionPin";
import { AGENT_VERSIONS_COPY as COPY } from "../workspace.constants";

const PREFLIGHT_ROLES = {
  pending: "info",
  failed: "danger",
  passed: "success",
};
export function PreflightBadge({ status }) {
  const label = COPY.preflight[status];
  if (!label) return null;
  return <VersionBadge label={label} role={PREFLIGHT_ROLES[status]} />;
}
PreflightBadge.propTypes = { status: PropTypes.string };

// The last check: each step's status and the names it found missing. A caller
// that knows the check failed passes `fallback` for a report with nothing in it.
export function PreflightReport({ report, fallback }) {
  const checks = Array.isArray(report?.checks) ? report.checks : [];
  const missing = Array.isArray(report?.missing) ? report.missing : [];
  const invalid = Array.isArray(report?.invalid) ? report.invalid : [];
  const line = (text, color = "text.secondary") => (
    <Typography sx={{ typography: "s3", color }}>{text}</Typography>
  );
  if (!checks.length && !missing.length && !invalid.length && !report?.error) {
    if (!fallback) return null;
    return (
      <Stack spacing={0.25} sx={{ mt: 0.5 }} data-testid="preflight-report">
        {line(fallback, "status.danger.fg")}
      </Stack>
    );
  }
  return (
    <Stack spacing={0.25} sx={{ mt: 0.5 }} data-testid="preflight-report">
      {checks.map((check) => (
        <Typography
          key={check.id || check.label}
          sx={{
            typography: "s3",
            color: check.status === "failed" ? "status.danger.fg" : "text.secondary",
          }}
        >
          {`${check.label}: ${COPY.checks[check.status] || check.status}`}
          {check.missing?.length ? ` · ${COPY.missing(check.missing)}` : ""}
        </Typography>
      ))}
      {missing.length > 0 && line(COPY.missing(missing), "status.danger.fg")}
      {invalid.length > 0 && line(COPY.invalid(invalid), "status.danger.fg")}
      {report.error && line(COPY.checkError(report.error), "status.danger.fg")}
    </Stack>
  );
}
PreflightReport.propTypes = {
  report: PropTypes.object,
  fallback: PropTypes.string,
};
