export const STATUS_TYPES = {
  PASS: "pass",
  ERROR: "error",
  UNSET: "unset",
};

export const STATUS_CONFIG = {
  [STATUS_TYPES.PASS]: {
    icon: "/assets/icons/status/success.svg",
    color: "status.success.fg",
    bgColor: "status.success.bg",
    borderColor: "status.success.border",
    textColor: "status.success.fg",
  },
  [STATUS_TYPES.ERROR]: {
    icon: "/assets/icons/status/error.svg",
    color: "status.danger.fg",
    bgColor: "status.danger.bg",
    borderColor: "status.danger.border",
    textColor: "status.danger.fg",
  },
  [STATUS_TYPES.UNSET]: {
    icon: "/assets/icons/status/unset.svg",
    color: "text.disabled",
    bgColor: "action.hover",
    borderColor: "text.disabled",
    textColor: "text.disabled",
  },
};

/**
 * Normalizes a value to lowercase string for comparison
 * @param {any} val - Value to normalize
 * @returns {string} Normalized lowercase string
 */
const normalize = (val) => val?.toString?.()?.toLowerCase?.() || "";

/**
 * Infers status type from a value using flexible string matching
 * @param {any} value - Value to infer status from
 * @returns {string} Status type (pass, error, or unset)
 */
const inferStatus = (value) => {
  const norm = normalize(value);
  if (!norm) return STATUS_TYPES.UNSET;
  if (norm.includes("pass") || norm.includes("ok")) return STATUS_TYPES.PASS;
  if (norm.includes("error") || norm.includes("fail"))
    return STATUS_TYPES.ERROR;
  return STATUS_TYPES.UNSET;
};

export const getStatusDetails = ({ status, label }) => {
  let finalStatus;
  let finalLabel;

  if (status !== undefined && status !== null) {
    // Case: explicit non-null status
    finalStatus = inferStatus(status);
    finalLabel = label || `Status: ${normalize(status).toUpperCase()}`;
  } else if (status === null) {
    // Case: status is explicitly null → treat as UNSET
    finalStatus = STATUS_TYPES.UNSET;
    finalLabel = label || "Status: UNSET";
  } else if (label !== undefined && label !== null) {
    // Case: only label is provided
    finalStatus = inferStatus(label);
    finalLabel = `Status: ${normalize(label).toUpperCase()}`;
  } else {
    // Case: nothing provided → error
    finalStatus = STATUS_TYPES.ERROR;
    finalLabel = "Status: ERROR";
  }

  return {
    finalStatus,
    finalLabel,
    config: getStatusConfig(finalStatus),
  };
};

/**
 * Gets status configuration object for a given status
 * @param {string} status - Status type
 * @returns {object} Status configuration object
 */
export const getStatusConfig = (status) => {
  return STATUS_CONFIG[status] || STATUS_CONFIG[STATUS_TYPES.UNSET];
};

/**
 * Validates if a status is a valid status type
 * @param {string} status - Status to validate
 * @returns {boolean} Whether the status is valid
 */
export const isValidStatus = (status) => {
  return Object.values(STATUS_TYPES).includes(status);
};

/**
 * Gets all available status types
 * @returns {string[]} Array of status types
 */
export const getAvailableStatuses = () => {
  return Object.values(STATUS_TYPES);
};
