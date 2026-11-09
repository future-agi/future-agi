/**
 * Extract a user-friendly error message from an API error object.
 * Handles the standard { response: { data: { result, message, error } } } shape
 * returned by axios mutation errors.
 */
export function getErrorMessage(error, fallback = "Something went wrong") {
  if (!error) return null;
  const data = error?.response?.data ?? error;
  const msg =
    data?.result ||
    data?.message ||
    data?.error ||
    data?.detail ||
    error?.message;
  return typeof msg === "string" ? msg : fallback;
}

/**
 * Unwrap a standard API response to get the inner result.
 * Handles { data: { result: ... } } or { data: ... } shapes.
 */
export function unwrapResponse(response) {
  const raw = response?.data;
  return raw?.result ?? raw;
}

// An S3 destination as the backend stores it
// (futureagi/integrations/services/destinations.py):
// https://<bucket>.s3.<region>.amazonaws.com/<prefix>.
const S3_HOST = /^(.+)\.s3\.([a-z0-9-]+)\.amazonaws\.com$/;

/**
 * Where a connection delivers, as the Connections card and the connection's
 * header show it: an S3 bucket as "s3://<bucket>/<prefix> in <region>", every
 * other connection as the address it stores.
 */
export function connectionDestination(connection) {
  const hostUrl = connection?.host_url || connection?.hostUrl || "";
  if (connection?.platform === "cloud_storage" && hostUrl) {
    try {
      const url = new URL(hostUrl);
      const s3 = S3_HOST.exec(url.hostname);
      if (s3) {
        const prefix = decodeURIComponent(url.pathname).replace(
          /^\/+|\/+$/g,
          "",
        );
        return `s3://${s3[1]}${prefix ? `/${prefix}` : ""} in ${s3[2]}`;
      }
    } catch {
      return hostUrl;
    }
  }
  return hostUrl;
}

/** The address a new S3 connection stores, as the backend derives it. */
export function s3Destination({ bucket, region, prefix }) {
  const path = (prefix || "")
    .split("/")
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  return `https://${bucket}.s3.${region}.amazonaws.com/${path}`;
}
