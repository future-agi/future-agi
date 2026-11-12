import axios from "src/utils/axios";

const parseJsonBody = (body) => {
  if (!body) return undefined;
  if (typeof body !== "string") return body;
  const trimmed = body.trim();
  return trimmed ? JSON.parse(trimmed) : undefined;
};

const headersToObject = (headers) => {
  if (!headers) return undefined;
  if (headers instanceof Headers) {
    return Object.fromEntries(headers.entries());
  }
  return headers;
};

/**
 * @typedef {{ data: unknown, status: number, headers: Headers }} ApiResponse
 */

/**
 * Orval supplies the operation's response union as T. Keep the Axios transport
 * and its metadata, but expose the Fetch Headers interface the client declares.
 * This is a transport adapter, not a replacement for response-schema validation.
 * @template [T=ApiResponse]
 * @param {string} url
 * @param {RequestInit} options
 * @returns {Promise<T>}
 */
export const apiMutator = async (url, options) => {
  const { body, headers, method, signal } = options;
  const data = parseJsonBody(body);
  const headerObject = headersToObject(headers);
  const config =
    signal || headerObject
      ? {
          ...(signal ? { signal } : {}),
          ...(headerObject ? { headers: headerObject } : {}),
        }
      : undefined;

  let response;
  switch (String(method || "").toUpperCase()) {
    case "GET":
      response = await axios.get(url, config);
      break;
    case "POST":
      response = config
        ? await axios.post(url, data, config)
        : await axios.post(url, data);
      break;
    case "PUT":
      response = config
        ? await axios.put(url, data, config)
        : await axios.put(url, data);
      break;
    case "PATCH":
      response = config
        ? await axios.patch(url, data, config)
        : await axios.patch(url, data);
      break;
    case "DELETE":
      response = await axios.delete(url, config);
      break;
    default:
      throw new Error(`Unsupported OpenAPI method: ${method || "<missing>"}`);
  }

  const responseHeaders = new Headers();
  for (const [name, value] of Object.entries(response.headers || {})) {
    if (value === undefined || value === null || value === false) continue;
    for (const entry of Array.isArray(value) ? value : [value]) {
      responseHeaders.append(name, String(entry));
    }
  }
  return /** @type {T} */ ({ ...response, headers: responseHeaders });
};

export default apiMutator;
