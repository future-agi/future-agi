import { format } from "date-fns";

export function isOrgApiKeyExpired(expiresAt, now = Date.now()) {
  if (!expiresAt) {
    return false;
  }
  const ms = new Date(expiresAt).getTime();
  return Number.isFinite(ms) && ms <= now;
}

export function formatApiKeyExpiry(expiresAt) {
  if (!expiresAt) {
    return "Never";
  }
  const date = new Date(expiresAt);
  if (Number.isNaN(date.getTime())) {
    return "Never";
  }
  return format(date, "MM-dd-yyyy");
}
