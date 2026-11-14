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
