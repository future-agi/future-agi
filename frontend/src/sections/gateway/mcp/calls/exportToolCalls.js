import axiosInstance from "src/utils/axios";

import { exportErrorMessage } from "../../logs/exportLogs";
import { MCP_API } from "../api";
import { toolCallExportParams } from "./toolCalls";

// The Tool calls view as a file: exactly the list's filters, CSV or NDJSON,
// at most the server's row cap (agentcc/services/mcp_tool_call_export.py).
// The server sends metadata columns only, never arguments, results or error
// text, and audits the download. A refusal (400, 503) resolves so its message
// can be read from the Blob body and shown.

export const EXPORT_FORMATS = [
  { value: "csv", label: "Export as CSV" },
  { value: "json", label: "Export as JSON" },
];

function saveBlob(blob, format) {
  const date = new Date().toISOString().split("T")[0];
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `mcp-tool-calls-${date}.${format === "json" ? "ndjson" : "csv"}`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/** Downloads the filtered view; throws an Error with the server's message. */
export async function downloadToolCallExport(filters, format) {
  const response = await axiosInstance.get(MCP_API.toolCallsExport, {
    params: { ...toolCallExportParams(filters), export_format: format },
    responseType: "blob",
    validateStatus: (status) =>
      status !== 401 && (status < 500 || status === 503),
  });
  if (response.status >= 400) {
    throw new Error(await exportErrorMessage({ response }));
  }
  saveBlob(response.data, format);
}
