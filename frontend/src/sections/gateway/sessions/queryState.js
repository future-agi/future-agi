// Only these fields belong to the Sessions investigation. Other route query
// parameters survive every update; they are never forwarded to session GETs.
const FIELDS = ["session", "status", "search", "page"];
const SESSION_ID = /^[a-zA-Z0-9_-]{1,128}$/;

export function readSessionQuery(params) {
  const session = params.get("session") || "";
  const status = params.get("status");
  const rawPage = params.get("page") || "";
  const page = Number(rawPage);
  return {
    // This is the database identity, not the public session_id used by Logs.
    session: SESSION_ID.test(session) ? session : null,
    status: status === "active" || status === "closed" ? status : "",
    search: params.get("search") || "",
    page:
      /^\d+$/.test(rawPage) && Number.isSafeInteger(page) && page > 0
        ? page
        : 1,
  };
}

export function updateSessionQuery(params, patch) {
  const next = new URLSearchParams(params);
  FIELDS.forEach((field) => {
    if (Object.prototype.hasOwnProperty.call(patch, field)) {
      if (patch[field] == null || patch[field] === "") next.delete(field);
      else next.set(field, String(patch[field]));
    }
  });
  // Canonicalize our fields (including malformed fields in an incoming URL),
  // leaving unrelated parameters, including repeated ones, untouched.
  const state = readSessionQuery(next);
  FIELDS.forEach((field) => {
    const value = state[field];
    if (value == null || value === "" || (field === "page" && value === 1)) {
      next.delete(field);
    } else {
      next.set(field, String(value));
    }
  });
  return next;
}
