// Tells a "this record is gone or not yours" refusal apart from any other
// failed read. The app's axios rejects with the server's error envelope plus
// `statusCode` (see utils/axios.js), so that is the shape read here.

export const RESOURCE_NOT_FOUND = "not_found";
export const RESOURCE_FORBIDDEN = "forbidden";

// The exact messages the current API sends with a 400 when the record is
// missing, deleted or in another organisation. Once the API answers these with
// a 404, the status alone is enough and these stay as a fallback.
const NOT_FOUND_MESSAGES = new Set([
  "the specified dataset does not exist or has been deleted.",
  "project not found",
]);

const messagesOf = (error) =>
  [error.message, error.detail, error.error, error.result]
    .filter((value) => typeof value === "string")
    .map((value) => value.trim().toLowerCase());

/**
 * @returns {"not_found" | "forbidden" | null} null for every other failure,
 * which callers keep handling with their usual retry or error state.
 */
export function getResourceUnavailableKind(error) {
  if (!error || typeof error !== "object") return null;

  const status = Number(error.statusCode ?? error.response?.status);

  if (status === 404) return RESOURCE_NOT_FOUND;
  // A 2FA-enforcement 403 is handled app-wide and is not about this record.
  if (status === 403 && error.code !== "2fa_required") {
    return RESOURCE_FORBIDDEN;
  }
  if (status === 400) {
    if (error.code === "not_found") return RESOURCE_NOT_FOUND;
    if (messagesOf(error).some((message) => NOT_FOUND_MESSAGES.has(message))) {
      return RESOURCE_NOT_FOUND;
    }
  }
  return null;
}
