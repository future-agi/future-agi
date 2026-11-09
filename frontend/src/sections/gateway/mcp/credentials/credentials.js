// How a vault credential reads (agentcc/models/mcp_credential.py).

export const KINDS = [
  { value: "bearer", label: "Bearer token" },
  { value: "api_key", label: "API key" },
];

export function kindLabel(kind) {
  return KINDS.find((k) => k.value === kind)?.label || kind || "—";
}

const STATUS = {
  active: { label: "Active", tone: "healthy" },
  revoked: { label: "Revoked", tone: "neutral" },
  expired: { label: "Expired", tone: "warning" },
  needs_reauth: { label: "Needs re-entry", tone: "warning" },
};

export function statusOf(status) {
  return STATUS[status] || { label: status || "Unknown", tone: "neutral" };
}

export const STATUS_OPTIONS = Object.entries(STATUS).map(
  ([value, { label }]) => ({
    value,
    label,
  }),
);

// The header a credential is sent in.
export function headerLabel(credential) {
  if (credential.header_name) return credential.header_name;
  return credential.kind === "bearer" ? "Authorization (Bearer)" : "—";
}

// The servers that already hold an active shared credential: the vault
// refuses a second one (rotate or revoke it instead).
export function serversWithActiveCredential(rows) {
  return new Set(
    rows
      .filter((row) => row.status === "active" && row.scope === "shared")
      .map((row) => row.server_id),
  );
}

// The create request (MCPCredentialCreateRequestSerializer rejects unknown
// fields): a header name only for an API key.
export function createBody({ serverId, kind, value, headerName, label }) {
  const body = { server_id: serverId, kind, value };
  if (kind === "api_key") body.header_name = headerName.trim() || "X-API-Key";
  body.label = label.trim();
  return body;
}
