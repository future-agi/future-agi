import PropTypes from "prop-types";

// Local PropTypes shapes for the Agents tab. The cards and drawers in this
// folder share these; the store's env-state slice holds the same fields
// (`agent`, `agentVersions`, `activeAgentVersion`).

// One entry in a source agent's own versions[] stack.
export const AGENT_VERSION_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  label: PropTypes.string,
  values: PropTypes.objectOf(PropTypes.any),
  via: PropTypes.string,
  connectedAt: PropTypes.string,
  note: PropTypes.string,
});

// A normalised agent — source or additional. Top-level `values`/`via`/`note`
// mirror the active version (see applyActiveVersion) so readers that don't know
// about versions keep working.
export const AGENT_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  typeId: PropTypes.string,
  values: PropTypes.objectOf(PropTypes.any),
  via: PropTypes.string,
  connectedAt: PropTypes.string,
  note: PropTypes.string,
  versions: PropTypes.arrayOf(AGENT_VERSION_SHAPE),
  activeVersionId: PropTypes.string,
  isSource: PropTypes.bool,
});

// The env-level version record the header pill and Overview summary read
// (toEnvAgentVersion output).
export const ENV_AGENT_VERSION_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  label: PropTypes.string,
  note: PropTypes.string,
  reach: PropTypes.string,
  createdAt: PropTypes.string,
});

// A single connection/source detail row (label + value, optional mono/copy).
export const AGENT_ROW_SHAPE = PropTypes.shape({
  label: PropTypes.string,
  value: PropTypes.node,
  mono: PropTypes.bool,
  copy: PropTypes.bool,
});

// The modality descriptor a card resolves from MODALITY[agent.typeId].
export const AGENT_TYPE_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  label: PropTypes.string,
  icon: PropTypes.string,
  color: PropTypes.string,
  channel: PropTypes.string,
});

// One scripted builder-turn step (matches the console step kinds:
// think | tool | note | json).
export const AGENT_STEP_SHAPE = PropTypes.shape({
  kind: PropTypes.oneOf(["think", "tool", "note", "json"]),
  text: PropTypes.string,
  label: PropTypes.string,
  result: PropTypes.string,
  value: PropTypes.string,
});
