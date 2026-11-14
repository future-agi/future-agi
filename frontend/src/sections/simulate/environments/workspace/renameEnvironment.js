// §8 rename validation, shared by the header dialog and the settings field.
// The name is trimmed and must be 1..255 chars; blank is rejected rather than
// treated as a reset (a blank would silently fall back to the derived name,
// which reads as the rename being ignored).
export const MAX_ENV_NAME = 255;

export function validateEnvName(raw) {
  const value = (raw ?? "").trim();
  if (!value) return { ok: false, value, error: "Name can't be blank." };
  if (value.length > MAX_ENV_NAME) {
    return { ok: false, value, error: `Name must be ${MAX_ENV_NAME} characters or fewer.` };
  }
  return { ok: true, value, error: null };
}
