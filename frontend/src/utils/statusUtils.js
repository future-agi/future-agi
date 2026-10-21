export const STATUS_TYPES = {
  PASS: "pass",
  ERROR: "error",
  UNSET: "unset",
  RUNNING: "running",
  CANCELED: "canceled",
};

export const STATUS_CONFIG = {
  [STATUS_TYPES.PASS]: {
    icon: "/assets/icons/status/success.svg",
    color: "green.500",
    bgColor: "green.o5",
    borderColor: "green.500",
    textColor: "green.500",
  },
  [STATUS_TYPES.ERROR]: {
    icon: "/assets/icons/status/error.svg",
    color: "red.500",
    bgColor: "red.o5",
    borderColor: "red.500",
    textColor: "red.500",
  },
  [STATUS_TYPES.UNSET]: {
    icon: "/assets/icons/status/unset.svg",
    color: "text.disabled",
    bgColor: "action.hover",
    borderColor: "text.disabled",
    textColor: "text.disabled",
  },
  // Stopped on purpose — neither a failure nor an unknown, so it gets its own hue rather
  // than the grey UNSET treatment it used to blend into. No cancel glyph ships under
  // icons/status, so the neutral one is reused.
  [STATUS_TYPES.CANCELED]: {
    icon: "/assets/icons/status/unset.svg",
    color: "orange.500",
    bgColor: "orange.o5",
    borderColor: "orange.500",
    // Light-mode value; CustomStatusChip swaps it for orange.300 in dark, where the
    // deep orange would sit at roughly 2:1 against the page.
    textColor: "orange.700",
  },
  // No in-progress glyph ships under icons/status, so the neutral one is reused.
  [STATUS_TYPES.RUNNING]: {
    icon: "/assets/icons/status/unset.svg",
    color: "blue.500",
    bgColor: "blue.o5",
    borderColor: "blue.500",
    textColor: "blue.500",
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
 * @returns {string} Status type (pass, error, running, or unset)
 */
const inferStatus = (value) => {
  const norm = normalize(value);
  if (!norm) return STATUS_TYPES.UNSET;
  if (norm.includes("pass") || norm.includes("ok")) return STATUS_TYPES.PASS;
  if (norm.includes("error") || norm.includes("fail"))
    return STATUS_TYPES.ERROR;
  if (norm.includes("cancel")) return STATUS_TYPES.CANCELED;
  // Matched after the terminal states so anything that already resolved to
  // pass/error keeps resolving there (e.g. "run failed" stays ERROR).
  if (norm.includes("running") || norm.includes("progress"))
    return STATUS_TYPES.RUNNING;
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
