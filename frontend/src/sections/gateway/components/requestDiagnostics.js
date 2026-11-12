import { getGatewayAuthenticationErrorMessage } from "src/utils/errorUtils";

import { readResponseEvidence, readResponseHeader } from "../utils/managedRefusalEvidence";

const object = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const parseBody = (value) => {
  if (typeof value !== "string") return object(value);
  try { return object(JSON.parse(value)); } catch { return { detail: value }; }
};
const text = (value) => typeof value === "string" ? value : "";
const redact = (value) => value
  .replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [redacted]")
  .replace(/\b(?:sk-|gw_live_|gw_test_)[A-Za-z0-9_-]+/g, "[redacted]");
const messageText = (value) => redact(text(value))
  // eslint-disable-next-line no-control-regex -- Strip control characters from displayed diagnostics.
  .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "")
  .slice(0, 2000);
const identifier = (value) => {
  const raw = text(value);
  return raw.length > 0 && raw.length <= 160 && /^[A-Za-z0-9_.:[\]/-]+$/.test(raw) && redact(raw) === raw
    ? raw : undefined;
};
const statusNumber = (value) => {
  if (typeof value !== "number" && !(typeof value === "string" && /^\d{3}$/.test(value))) return undefined;
  const status = Number(value);
  return Number.isInteger(status) && status >= 100 && status < 600 ? status : undefined;
};
const retryAfterValue = (value) => {
  const raw = typeof value === "number" && Number.isFinite(value) ? String(value) : text(value);
  // An actual Retry-After field can be seconds or an HTTP date, never prose.
  return /^(0|[1-9]\d{0,9})$/.test(raw) ||
    (/^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(raw) && Number.isFinite(Date.parse(raw)))
    ? raw : undefined;
};

/**
 * Safe evidence only, for a relay result, shared console error, or raw Axios
 * rejection. Never serialize the input: config, credentials, arbitrary payloads
 * and body-supplied request/timing aliases are not diagnostics. No I/O or retry.
 * `refused` records definite 4xx refusal evidence, NOT proof of no dispatch or charge.
 * Generic console 400s can wrap post-dispatch failures. Like transport errors,
 * timeouts and 5xx responses, they leave completion unknown.
 */
export function requestFailureFacts(errorOrResult) {
  const input = object(errorOrResult);
  const response = object(input.response);
  const outer = parseBody(response.data ?? input);
  const nested = object(outer.result);
  const relay = nested.status_code != null || nested.body != null ? nested
    : outer.status_code != null || outer.body != null ? outer : null;
  const body = relay ? parseBody(relay.body) : outer;
  const error = object(body.error);
  const status = statusNumber(relay?.status_code ?? response.status ?? input.statusCode ?? outer.statusCode);
  const headers = relay ? relay.guardrail_headers ?? relay.headers
    : response.headers ?? input.responseHeaders ?? input.guardrail_headers ?? input.headers;
  // Preserve the application's controlled authentication remedy in both the
  // visible copy and diagnostics, never re-expose the rejected upstream prose.
  const message = getGatewayAuthenticationErrorMessage({ response: { status, data: body } }) || messageText(
    text(error.message) || text(body.error) || text(body.detail) || text(body.message) ||
    text(body.result) || text(input.message),
  ) || "The request failed.";
  const code = identifier(error.code ?? body.code);
  const type = identifier(error.type);
  const param = identifier(error.param);
  const requestId = readResponseEvidence(headers).requestId || identifier(readResponseHeader(headers, "x-request-id"));
  const retryAfter = retryAfterValue(readResponseHeader(headers, "retry-after"));
  // GeneralMethods.bad_request uses default codes even for a relay exception
  // after dispatch. Neither those codes nor its validation_error type/prose
  // prove refusal; an actual relayed status or a specific code is distinct.
  const ambiguousConsoleError = !relay && body.status === false &&
    (!code || code === "invalid" || code === "error");
  return {
    outcome: status >= 400 && status < 500 && status !== 408 && status !== 499 && !ambiguousConsoleError ? "refused" : "unknown",
    ...(status !== undefined ? { status } : {}),
    message,
    // Axios transport codes are not server error codes.
    ...(code && !/^(ERR_|ECONN|ETIMEDOUT)/.test(code) ? { code } : {}),
    ...(type ? { type } : {}),
    ...(param ? { param } : {}),
    ...(requestId ? { requestId } : {}),
    ...(retryAfter !== undefined ? { retryAfter } : {}),
  };
}
