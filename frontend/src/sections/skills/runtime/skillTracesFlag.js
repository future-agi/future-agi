import { useEffect, useState } from "react";
import { isFeatureEnabled, onFeatureFlags } from "src/utils/PostHog/posthog";

// The `skillTraces` UI flag (SKILL-TRACES §7, §10 "The skillTraces flag",
// verification item V12).
//
// V12 finding: at 8254acd488 the Skills UI had no feature-flag mechanism of
// its own, and nothing in the app called the PostHog flag helpers. This module
// is therefore the flag's single definition. Lane ST10 owns it; ST9 and UXF5
// only read it through `useSkillTracesEnabled()` or `isSkillTracesEnabled()`.
//
// The flag is OFF unless one of these turns it on:
//   1. the deployment build sets VITE_SKILL_TRACES=on (or true/1), which is
//      how the standalone e2e stack and self-hosted installs enable it, since
//      PostHog is not initialised there;
//   2. the PostHog feature flag `skillTraces` is on for the workspace.
// Tests use `setSkillTracesOverride(true | false | null)`.
//
// The flag only hides or shows screens. Every runtime read is still
// authorized by the server against the projects the person can open.

export const SKILL_TRACES_FLAG = "skillTraces";

const ON_VALUES = new Set(["on", "true", "1"]);
let override = null;
const listeners = new Set();

function buildSetting() {
  try {
    const value = import.meta.env.VITE_SKILL_TRACES;
    return ON_VALUES.has(String(value ?? "").trim().toLowerCase());
  } catch {
    return false;
  }
}

/** Synchronous read, for helpers that are not hooks (for example traces.js). */
export function isSkillTracesEnabled() {
  if (override !== null) return override;
  if (buildSetting()) return true;
  try {
    return Boolean(isFeatureEnabled(SKILL_TRACES_FLAG));
  } catch {
    return false;
  }
}

/** Test and preview seam. `null` restores the real setting. */
export function setSkillTracesOverride(value) {
  override = value === null || value === undefined ? null : Boolean(value);
  listeners.forEach((listener) => listener());
}

/** Hook form: re-renders when PostHog flags load or the override changes. */
export function useSkillTracesEnabled() {
  const [enabled, setEnabled] = useState(isSkillTracesEnabled);
  useEffect(() => {
    let active = true;
    const update = () => {
      if (active) setEnabled(isSkillTracesEnabled());
    };
    listeners.add(update);
    let unsubscribe;
    try {
      unsubscribe = onFeatureFlags(update);
    } catch {
      unsubscribe = undefined;
    }
    update();
    return () => {
      active = false;
      listeners.delete(update);
      if (typeof unsubscribe === "function") unsubscribe();
    };
  }, []);
  return enabled;
}
