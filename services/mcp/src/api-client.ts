import { authorizationHeaders } from "./auth.js";
import type { ApiCredentials, GeneratedTool } from "./types.js";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly details: unknown,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export interface ApiClientOptions {
  baseUrl: string;
  timeoutMs: number;
  maxResponseBytes: number;
  fetchImplementation?: typeof fetch;
}

function queryValue(value: unknown): string {
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return String(value);
  }
  return JSON.stringify(value);
}

async function readBoundedBody(
  response: Response,
  maxBytes: number,
): Promise<Uint8Array> {
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) {
        await reader.cancel("MCP response size limit exceeded");
        throw new ApiRequestError(
          "FutureAGI API response is too large; use narrower filters or pagination",
          413,
          { max_response_bytes: maxBytes },
        );
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const output = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return output;
}

export async function executeApiTool(
  tool: GeneratedTool,
  arguments_: Record<string, unknown>,
  credentials: ApiCredentials,
  options: ApiClientOptions,
): Promise<{ status: number; data: unknown }> {
  let requestPath = tool.path;
  const query = new URLSearchParams();
  let body: unknown;

  for (const [name, value] of Object.entries(arguments_)) {
    if (value === undefined) continue;
    const location = tool.parameterLocations[name];
    if (location === "path") {
      requestPath = requestPath.replace(`{${name}}`, encodeURIComponent(String(value)));
    } else if (location === "query") {
      if (Array.isArray(value)) {
        for (const item of value) query.append(name, queryValue(item));
      } else {
        query.set(name, queryValue(value));
      }
    } else if (location === "body") {
      body = value;
    }
  }

  if (/\{[^}]+\}/u.test(requestPath)) {
    throw new ApiRequestError("A required path parameter is missing", 400, null);
  }

  const url = new URL(requestPath, `${options.baseUrl}/`);
  url.search = query.toString();
  const headers = authorizationHeaders(credentials);
  if (body !== undefined) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await (options.fetchImplementation ?? fetch)(url, {
      method: tool.method,
      headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      redirect: "error",
      signal: AbortSignal.timeout(options.timeoutMs),
    });
  } catch (error) {
    const timedOut =
      error instanceof Error &&
      (error.name === "AbortError" || error.name === "TimeoutError");
    throw new ApiRequestError(
      timedOut ? "FutureAGI API request timed out" : "FutureAGI API is unavailable",
      timedOut ? 504 : 502,
      null,
    );
  }

  const declaredSize = Number.parseInt(
    response.headers.get("content-length") ?? "0",
    10,
  );
  if (declaredSize > options.maxResponseBytes) {
    throw new ApiRequestError(
      "FutureAGI API response is too large; use narrower filters or pagination",
      413,
      { max_response_bytes: options.maxResponseBytes },
    );
  }
  const bytes = await readBoundedBody(response, options.maxResponseBytes);
  const text = new TextDecoder().decode(bytes);
  let data: unknown = text || null;
  if (text && (response.headers.get("content-type") ?? "").includes("application/json")) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new ApiRequestError("FutureAGI API returned invalid JSON", 502, null);
    }
  }
  if (!response.ok) {
    throw new ApiRequestError(
      `FutureAGI API returned HTTP ${response.status}`,
      response.status,
      data,
    );
  }
  return { status: response.status, data };
}
