// Single source of truth for the Tracing test-tab mapping/project that Save
// Version persists and version-select restores. The backend stores these as
// their own `mapping` / `tracing_project_id` fields on the eval version
// (snake_case), so both sides read the exact same keys — a hand-copied shape
// here would silently load empty.

// Build the Save-Version payload fragment. Only include a key when it has a
// real value: the backend treats an absent key as "leave the existing
// mapping/project untouched", so we never wipe on a save from a tab that has
// no mapping state.
export const buildVersionMappingPayload = (mapping, tracingProjectId) => {
  const payload = {};
  if (mapping && typeof mapping === "object") {
    payload.mapping = { ...mapping };
  }
  if (tracingProjectId) {
    payload.tracing_project_id = tracingProjectId;
  }
  return payload;
};

// Read a loaded version's saved mapping, defaulting to an empty object so an
// absent/NULL mapping (pre-snapshot versions) seeds the tab as "unmapped"
// rather than throwing.
export const resolveVersionMapping = (version) => {
  const m = version && version.mapping;
  return m && typeof m === "object" ? m : {};
};

// Read a loaded version's saved tracing project id, or null when none.
export const resolveVersionTracingProjectId = (version) =>
  (version && version.tracing_project_id) || null;
