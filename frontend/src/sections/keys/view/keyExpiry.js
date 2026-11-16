import { endOfDay } from "date-fns";

export const parseExpiry = (value) => {
  if (!value) return null;
  const date = new Date(value);
  return isNaN(date.getTime()) ? null : date;
};

// The server's is_expired wins; the local clock is only a fallback for rows
// without it.
export const isKeyExpired = ({
  expires_at: expiresAt,
  is_expired: isExpired,
}) =>
  typeof isExpired === "boolean"
    ? isExpired
    : (parseExpiry(expiresAt)?.getTime() ?? Infinity) <= Date.now();

// A key picked to expire "on" a date stays valid through the end of that day
// in the admin's timezone. Null means the key never expires.
export const expiresAtFromDate = (date) =>
  date && !isNaN(date.getTime()) ? endOfDay(date).toISOString() : null;
