// W5 paths (endpoints.develop.w5) arrive with the W5 registry commit
// (W5-SCOPE §3.1). Until a path is registered, the door that needs it stays
// absent: these helpers say whether it exists and build it.
import { endpoints } from "src/utils/axios";

/** The registered W5 path builder (or string) named `name`, else null. */
export const w5Endpoint = (name) => endpoints?.develop?.w5?.[name] ?? null;

export const hasW5 = (name) => Boolean(w5Endpoint(name));

/** The W5 path for `name` with its arguments, or null. */
export function w5Path(name, ...args) {
  const ep = w5Endpoint(name);
  if (!ep) return null;
  return typeof ep === "function" ? ep(...args) : ep;
}

/** An API error's message (the mock's `{status:false, result}` envelope). */
export const apiErrorText = (e) =>
  e?.response?.data?.result ??
  (typeof e?.result === "string" ? e.result : null) ??
  e?.message ??
  "The request failed.";
