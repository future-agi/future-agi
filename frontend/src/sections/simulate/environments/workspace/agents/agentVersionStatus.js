import {
  AGENT_VERSIONS_COPY as COPY,
  VERSIONS_COPY,
} from "../workspace.constants";

export const named = (version) => version?.label || VERSIONS_COPY.unlabelled;

// Why a version whose check passed is not this environment's default.
export const activationText = (version) => {
  const code = version?.activationOutcome;
  if (
    !code ||
    version.isDefault ||
    version.status !== "active" ||
    code === "activated" ||
    code === "already_default"
  )
    return null;
  return COPY.activation[code] || COPY.activationOther(code);
};

// Names the version's source reads but its credentials lack.
export const missingRequired = (version) => {
  const required = version?.requiredVariableNames;
  if (!Array.isArray(required)) return [];
  const have = new Set(version.credentialNames || []);
  return required.filter((name) => !have.has(name));
};

// Names the version's last check found missing.
export const checkMissing = (version) => {
  const report = version?.preflightReport;
  if (!report) return [];
  const checks = Array.isArray(report.checks) ? report.checks : [];
  return [
    ...checks
      .filter((check) => check?.status === "failed")
      .flatMap((check) => check.missing || []),
    ...(Array.isArray(report.missing) ? report.missing : []),
  ].map(String);
};
