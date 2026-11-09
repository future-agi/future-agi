// The Hub's thin layer over the app's own axios instance (SPEC §14.2: the
// default export of src/utils/axios.js, with its session and refresh). It
// turns the app's rejected value into one HubApiError shape.
import axios from "src/utils/axios";

export class HubApiError extends Error {
  constructor({ status, code, message, fix, body }) {
    super(message || code || `Request failed (${status ?? "network"})`);
    this.name = "HubApiError";
    this.status = status ?? null;
    this.code = code ?? null;
    this.fix = fix ?? "";
    this.body = body ?? null;
  }

  get tombstone() {
    return this.body?.tombstone ?? null;
  }

  get movedTo() {
    return this.body?.movedTo ?? null;
  }
}

// The app's interceptor rejects with `{...response.data, statusCode}`; a
// request that never got a response carries only `message`.
export function toHubError(raw) {
  if (raw instanceof HubApiError) return raw;
  const status = raw?.statusCode ?? raw?.response?.status ?? null;
  const envelope = raw?.error && typeof raw.error === "object" ? raw.error : null;
  const body = raw && typeof raw === "object" ? { ...raw } : null;
  if (body) delete body.statusCode;
  return new HubApiError({
    status,
    code: envelope?.code ?? null,
    message:
      envelope?.message ??
      (typeof raw?.detail === "string" ? raw.detail : null) ??
      (status ? null : raw?.message) ??
      null,
    fix: envelope?.fix ?? "",
    body,
  });
}

async function run(request) {
  try {
    const response = await request();
    return response.data;
  } catch (raw) {
    throw toHubError(raw);
  }
}

const cleanParams = (params = {}) =>
  Object.fromEntries(
    Object.entries(params).filter(
      ([, value]) => value !== undefined && value !== null && value !== "",
    ),
  );

export const hubGet = (url, params) =>
  run(() => axios.get(url, { params: cleanParams(params) }));

export const hubGetText = (url) =>
  run(() =>
    axios.get(url, {
      responseType: "text",
      transformResponse: [(data) => data],
    }),
  );

export const hubPost = (url, data) => run(() => axios.post(url, data ?? {}));
export const hubPatch = (url, data) => run(() => axios.patch(url, data ?? {}));
export const hubDelete = (url) => run(() => axios.delete(url));

export const hubUpload = (url, formData, onUploadProgress) =>
  run(() => axios.post(url, formData, { onUploadProgress }));
