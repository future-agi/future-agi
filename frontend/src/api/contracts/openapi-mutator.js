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

  const response =
    method === "GET"
      ? await axios.get(url, config)
      : method === "POST"
        ? config
          ? await axios.post(url, data, config)
          : await axios.post(url, data)
        : method === "PUT"
          ? config
            ? await axios.put(url, data, config)
            : await axios.put(url, data)
          : method === "PATCH"
            ? config
              ? await axios.patch(url, data, config)
              : await axios.patch(url, data)
            : await axios.delete(url, config);

  return response;
};

export default apiMutator;
