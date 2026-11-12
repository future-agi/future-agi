import axiosInstance, { endpoints } from "src/utils/axios";

import { requestLogQueryParams } from "./hooks/useRequestLogs";

// Export of the request logs the table lists (CSV or JSON). The Export
// dialog counts the matching requests first, says what the file will hold,
// then downloads exactly the table's query: its filters, its search (as q)
// and its order. The server refuses an export with no rows, or one over its
// row cap unless max_rows asks for the first rows; the app's axios
// interceptor drops a blob error body, so a refusal (400) resolves and its
// message is read from the body and shown, never swallowed.

// The most rows one export holds: futureagi/agentcc/services/export.py
// MAX_EXPORT_ROWS. Above it the server streams the first EXPORT_ROW_CAP rows
// in the table's order when asked (max_rows), and the dialog says so first.
export const EXPORT_ROW_CAP = 10000;

const FALLBACK = "The export could not be made. Try again.";

/** The query an export of these filters sends: the table's own query. */
export function exportParams(filters = {}) {
  return requestLogQueryParams(filters);
}

const textOf = (body) => {
  if (!body || typeof body !== "object") {
    return typeof body === "string" && body.trim() ? body : null;
  }
  for (const key of ["message", "detail", "result", "error"]) {
    const value = body[key];
    if (typeof value === "string" && value.trim()) return value;
    if (value && typeof value.message === "string") return value.message;
  }
  return null;
};

// Blob.text() is missing in some browsers (and jsdom); FileReader is not.
function readBlob(blob) {
  if (typeof blob.text === "function") return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

/** The message to show for a failed export (an error, or a response with a blob body). */
export async function exportErrorMessage(failure) {
  const data = failure?.response?.data ?? failure?.data;
  if (data && typeof Blob !== "undefined" && data instanceof Blob) {
    try {
      const raw = await readBlob(data);
      try {
        return textOf(JSON.parse(raw)) || FALLBACK;
      } catch {
        return raw.trim() ? raw.trim().slice(0, 300) : FALLBACK;
      }
    } catch {
      return FALLBACK;
    }
  }
  return textOf(data) || textOf(failure) || FALLBACK;
}

function saveBlob(blob, format) {
  const date = new Date().toISOString().split("T")[0];
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `ai-gateway-logs-${date}.${format}`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Downloads the export of the table's query, at most EXPORT_ROW_CAP rows in
 * the table's order; throws an Error carrying the server's message.
 */
export async function downloadLogExport(filters, format) {
  const response = await axiosInstance.get(endpoints.gateway.requestLogExport, {
    params: {
      ...exportParams(filters),
      export_format: format,
      max_rows: EXPORT_ROW_CAP,
    },
    responseType: "blob",
    // A refusal (400) resolves, so its body (a Blob) can be read; a 401
    // still goes through the session refresh, a 5xx through the app's error
    // path.
    validateStatus: (status) => status !== 401 && status < 500,
  });
  if (response.status >= 400) {
    throw new Error(await exportErrorMessage({ response }));
  }
  saveBlob(response.data, format);
}
