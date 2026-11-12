// Header bags come from an HTTP response, or the playground relay's upstream
// guardrail_headers. Never pass error.metadata or a response body to this reader.
export function readResponseHeader(headers, name) {
  const keys = Object.keys(headers || {}).filter(
    (candidate) => candidate.toLowerCase() === name.toLowerCase(),
  );
  // Do not pick the first of duplicate spellings, even when values agree.
  if (keys.length > 1) return undefined;
  if (typeof headers?.get === "function") return headers.get(name);
  return keys.length === 1 ? headers[keys[0]] : undefined;
}

export function readResponseEvidence(headers) {
  const raw = readResponseHeader(headers, "x-agentcc-dispatched");
  const requestId = readResponseHeader(headers, "x-agentcc-request-id");
  return {
    // Missing/malformed is unknown, never false. This evidence is informational:
    // it does not authorize replay or assert that a request was/was not charged.
    dispatched: raw === "true" ? true : raw === "false" ? false : "unknown",
    requestId:
      typeof requestId === "string" &&
      requestId.length === 26 &&
      /^[0-7][0-9A-HJKMNP-TV-Z]{25}$/.test(requestId)
        ? requestId
        : null,
  };
}
