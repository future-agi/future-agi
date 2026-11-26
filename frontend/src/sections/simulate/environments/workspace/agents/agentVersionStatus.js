import {
  AGENT_VERSIONS_COPY as COPY,
  VERSIONS_COPY,
} from "../workspace.constants";

// A version as it reads wherever it is listed, picked or saved: its number, then its name when it has one.
export const named = (version) =>
  [version?.label || VERSIONS_COPY.unlabelled, version?.name]
    .filter(Boolean)
    .join(" · ");

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
