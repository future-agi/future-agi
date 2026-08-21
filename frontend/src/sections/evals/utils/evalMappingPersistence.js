// Single source of truth for the test-tab mapping/project that Save Version
// persists and version-select restores. The backend keeps `mapping` as an
// opaque JSONField, so this shape is owned entirely by the frontend.
//
// Dataset and Tracing map the same variables to different value spaces (column
// ids vs. dotted trace-field paths), and a flat `{variable: value}` blob cannot
// say which wrote it — so `mapping` is an envelope keyed by mode.
export const MAPPING_MODE = {
  TRACING: "tracing",
  DATASET: "dataset",
};

export const MAPPING_MODES = [MAPPING_MODE.TRACING, MAPPING_MODE.DATASET];

const isPlainObject = (v) => !!v && typeof v === "object" && !Array.isArray(v);

const isNonEmptyObject = (v) => isPlainObject(v) && Object.keys(v).length > 0;

// An envelope holds ONLY mode keys, each an object; a flat mapping holds
// field-path strings. So the two are never ambiguous.
const isModeEnvelope = (raw) => {
  if (!isNonEmptyObject(raw)) return false;
  return Object.keys(raw).every(
    (k) =>
      MAPPING_MODES.includes(k) && (raw[k] === null || isPlainObject(raw[k])),
  );
};

// `null` means "leave the saved value alone" — the backend gates on
// `is not None`, so `{}` is truthy, passes that gate, and overwrites a real
// saved mapping with nothing.
export const buildVersionMappingPayload = (mappingByMode, tracingProjectId) => {
  const envelope = {};
  MAPPING_MODES.forEach((mode) => {
    const m = mappingByMode && mappingByMode[mode];
    if (isNonEmptyObject(m)) envelope[mode] = { ...m };
  });
  return {
    mapping: Object.keys(envelope).length > 0 ? envelope : null,
    tracing_project_id: tracingProjectId || null,
  };
};

// Defaults to {} so a NULL mapping (pre-snapshot versions) seeds the tab as
// unmapped rather than throwing.
export const resolveVersionMapping = (version, mode) => {
  const raw = version && version.mapping;
  if (!isNonEmptyObject(raw)) return {};
  if (isModeEnvelope(raw)) {
    const bucket = raw[mode];
    return isNonEmptyObject(bucket) ? bucket : {};
  }
  // A flat row predating the envelope carries no discriminator, so route it by
  // the only signal it has: a saved tracing project means Tracing wrote it.
  const legacyMode = resolveVersionTracingProjectId(version)
    ? MAPPING_MODE.TRACING
    : MAPPING_MODE.DATASET;
  return mode === legacyMode ? raw : {};
};

// Idempotent, so a test mode can normalize its prop without any call site
// having to know the shape: a flat mapping comes back unchanged.
export const unwrapModeMapping = (mapping, mode) => {
  if (!isNonEmptyObject(mapping)) return {};
  if (!isModeEnvelope(mapping)) return { ...mapping };
  return isNonEmptyObject(mapping[mode]) ? { ...mapping[mode] } : {};
};

// Read a loaded version's saved tracing project id, or null when none.
export const resolveVersionTracingProjectId = (version) =>
  (version && version.tracing_project_id) || null;

// Drop entries whose variable no longer exists. Variables are parsed live from
// the editor while `mapping` comes off the version row, so the two drift.
export const reconcileMappingToVariables = (mapping, variables) => {
  if (!isNonEmptyObject(mapping)) return {};
  // Pruning against an unknown set would wipe a valid saved mapping.
  if (!Array.isArray(variables) || variables.length === 0)
    return { ...mapping };
  const allowed = new Set(variables);
  return Object.fromEntries(
    Object.entries(mapping).filter(([variable]) => allowed.has(variable)),
  );
};
