// A stand-in for the app's axios instance (src/utils/axios.js) in Hub tests.
// It answers from a route table and rejects the way the app's interceptor
// does: with `{...response.data, statusCode}`.
import { vi } from "vitest";

let table = [];
export const calls = [];

// routes: [[method, path or RegExp, body or (config) => body, status?]]
export function respond(routes) {
  table = routes;
  calls.length = 0;
}

function handle(method) {
  return vi.fn(async (url, dataOrConfig, maybeConfig) => {
    const path = String(url).split("?")[0];
    const config = method === "get" || method === "delete" ? dataOrConfig : maybeConfig;
    const data = method === "get" || method === "delete" ? undefined : dataOrConfig;
    calls.push({ method, path, data, params: config?.params });
    const route = table.find(
      ([m, match]) => m === method && (match instanceof RegExp ? match.test(path) : match === path),
    );
    if (!route) return Promise.reject({ error: { code: "TEST_NO_ROUTE", message: `${method} ${path}` }, statusCode: 404 });
    const [, , body, status = 200] = route;
    const value = typeof body === "function" ? body({ path, data, config }) : body;
    if (status >= 400) return Promise.reject({ ...value, statusCode: status });
    return { data: value, status };
  });
}

const axiosMock = {
  get: handle("get"),
  post: handle("post"),
  patch: handle("patch"),
  delete: handle("delete"),
};

export default axiosMock;
