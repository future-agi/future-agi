// The console uses the explicit atomic replacement command on the create API.
// There is no multi-request fallback for an older or unacknowledging server.
// A failed response does not prove that no remote side effect occurred.

// Site wording binding: KEYS-ROTATE-1.
export const ROTATE_DIALOG_MESSAGE =
  "Rotation tries to create a new key and revoke the old key before showing the new key. If a new key is shown, copy it before closing and update your apps to use it. The old key may stop working before you switch your apps, or may keep working if revocation is delayed or fails.";

// Site wording bindings: KEYS-ROTATE-11 through KEYS-ROTATE-13.
export const ROTATE_REVOCATION_COPY = {
  local: (name) => `${name}'s old key is marked as revoked in the console. This alone does not confirm that the gateway will reject it.`,
  confirmed:
    "The gateway acknowledged the old-key revocation request. Some requests using the old key may still be accepted.",
  pending:
    "Gateway revocation could not be confirmed. Requests using the old key may still be accepted.",
};

// Canonical user IDs establish ownership; the contact label never does.
export function canRotate(key, actingUserId) {
  return Boolean(
    typeof actingUserId === "string" &&
      actingUserId &&
      key?.user === actingUserId &&
      key?.status === "active",
  );
}

// Key settings the gateway reads from key metadata. The create serializer
// takes them as their own fields and refuses them inside raw metadata.
const SETTINGS = [
  "log_content",
  "rate_limit_rpm",
  "rate_limit_tpm",
  "allowed_ips",
];

// Names that set a key's authority or identity (agentcc/validators.py
// RESERVED_KEY_METADATA, the gateway's keystore list), org_id aside: the
// server binds that one. The create refuses them in metadata, and dropping
// one would change what the new key may do (team picks its RBAC team, model
// list and budget), so a key that holds one is not rotated here.
export const RESERVED_KEY_METADATA = new Set([
  "access_groups",
  "type",
  "key_type",
  "role",
  "allow_policy_override",
  "team",
  "id",
  "name",
  "owner",
  "mcp_personal",
]);

function reservedNames(key) {
  return Object.keys(key.metadata || {})
    .filter((name) => RESERVED_KEY_METADATA.has(name))
    .sort();
}

const joinNames = (names) =>
  names.length < 2
    ? names.join("")
    : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;

/** The create-key body for a replacement of `key`. */
export function replacementPayload(key) {
  const metadata = { ...(key.metadata || {}) };
  // Bound by the server to the org the key is created in.
  delete metadata.org_id;
  const payload = {
    name: key.name,
    owner: key.owner || "",
  };
  if (key.project) payload.project_id = key.project;
  payload.allowed_models = key.allowedModels ?? key.allowed_models ?? [];
  payload.allowed_providers =
    key.allowedProviders ?? key.allowed_providers ?? [];
  SETTINGS.forEach((field) => {
    if (!(field in metadata)) return;
    const value = metadata[field];
    delete metadata[field];
    if (value === null || value === undefined || value === "") return;
    if (field === "allowed_ips") {
      // Stored comma-joined, as synced metadata holds it; sent as a list.
      payload.allowed_ips = String(value)
        .split(",")
        .map((entry) => entry.trim())
        .filter(Boolean);
    } else if (field.startsWith("rate_limit_")) {
      payload[field] = Number(value);
    } else {
      payload[field] = value;
    }
  });
  payload.metadata = metadata;
  return payload;
}

/** One explicit create/rotate command; never fall back to separate requests. */
export async function rotateApiKey(key, { replaceAtomically }) {
  const reserved = reservedNames(key);
  if (reserved.length) {
    throw new Error(
      `its metadata sets ${joinNames(reserved)}, which the console cannot copy to a new key. Rotate it where it was created`,
    );
  }
  if (typeof replaceAtomically !== "function") {
    throw new Error("Key rotation is not permitted");
  }
  let created;
  try {
    created = await replaceAtomically(replacementPayload(key));
  } catch (error) {
    if (error?.response?.data?.code === "KEY_PROVISIONING_FAILED") {
      // Empty detail selects the already-activated KEYS-ROTATE-10 fallback.
      throw new Error("");
    }
    throw error;
  }
  if (!created?.rotation || !created?.key || created.status !== "active") {
    throw new Error("Key rotation is not permitted");
  }
  const revoked = created.rotation;
  const oldRevoked = revoked.status === "revoked";
  const revokeWarning = revoked.warning || null;
  return {
    secret: created.key,
    created,
    expiryCopied: revoked.expiry_copied === true,
    oldRevoked,
    gatewayRevoked:
      oldRevoked && revoked.gateway_revocation_confirmed === true && !revokeWarning,
    revokeWarning,
  };
}
