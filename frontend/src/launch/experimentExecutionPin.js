import { isSurfaceShown } from "./launchSwitch";

// J09 REVISED / F65: dataset version selection pre-exists; experiment
// execution pins do not. Keep the latter out of the classic creation wire,
// including cached form data, without mutating the original configuration.
export function withoutExecutionPin(config) {
  if (!config || typeof config !== "object") return config;
  const result = { ...config };
  delete result.version_pin;
  return result;
}

export function withoutExperimentExecutionPin(entry) {
  const result = { ...entry, config: withoutExecutionPin(entry.config) };
  delete result.pinned_version_id;
  return result;
}

export function classicExperimentEvalSelection(payload, source) {
  // EXC7's reconciled binding owns stored-pin preservation and deliberate
  // definition-edit unpinning. Do not treat an edit as a new wizard pin.
  if (
    source !== "experiment" ||
    isSurfaceShown("exp.new") ||
    payload.bindingPayload
  )
    return payload;
  const result = withoutExperimentExecutionPin(payload);
  delete result.versionId;
  delete result.versionPin;
  return result;
}
