import { fDate, fDateTime, fToNowStrict } from "src/utils/format-time";

import { AVATAR_COLORS } from "./constants";

export function getAvatarColor(name) {
  let hash = 0;
  for (let i = 0; i < (name || "").length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

export function getInitials(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

export function timeAgo(date) {
  if (!date) return "";
  try {
    return fToNowStrict(date);
  } catch {
    return "";
  }
}

export function getDashboardCreatorName(db) {
  const name = db?.created_by?.name;
  return typeof name === "string" ? name.trim() : "";
}

export function getDashboardCreatorLabel(db) {
  return getDashboardCreatorName(db) || "Unknown creator";
}

export function formatDashboardListDate(date) {
  if (!date) return "—";
  try {
    return fDate(date) || "—";
  } catch {
    return "—";
  }
}

export function formatDashboardTooltipDate(date) {
  if (!date) return "";
  try {
    return fDateTime(date) || "";
  } catch {
    return "";
  }
}

export function formatDashboardWidgetCount(count) {
  const numericCount = Number(count || 0);
  const safeCount = Number.isFinite(numericCount) ? numericCount : 0;

  return `${safeCount} widget${safeCount === 1 ? "" : "s"}`;
}

export function getDashboardViewers(db) {
  const users = [];
  const seen = new Set();
  const creatorEmail = db?.created_by?.email;
  const addUser = (u, time) => {
    if (!u || !u.email || seen.has(u.email)) return;
    seen.add(u.email);
    const name = typeof u.name === "string" ? u.name.trim() : "";
    users.push({
      ...u,
      displayName:
        name || (u.email === creatorEmail ? "Unknown creator" : "Unknown user"),
      // Keep anonymous people visually distinct without exposing their email.
      // Named users retain the existing name-based colour assignment.
      avatarKey: name || u.email,
      time,
    });
  };
  addUser(db.updated_by, db.updated_at);
  addUser(db.created_by, db.created_at);
  return users;
}

export function getDashboardPeopleSummary(db) {
  const count = getDashboardViewers(db).length;
  if (!count) return "No people";

  return `${count} ${count === 1 ? "person" : "people"}`;
}

function getStablePrivateIdentifier(value) {
  // FNV-1a gives us a compact deterministic identifier. Only the derived token
  // is rendered, so the underlying email remains private.
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }

  return (hash >>> 0).toString(36).padStart(7, "0").slice(-7).toUpperCase();
}

/**
 * Label creator filter entries without exposing anonymous creators' emails.
 *
 * Each anonymous label always includes an identifier derived from the creator's
 * email. Unlike a rank within the current list, that identifier does not change
 * when another creator is added or removed during a refetch.
 */
export function labelCreatorsWithStableUnknownIdentifier(entries) {
  return entries.map((creator) => {
    const name =
      typeof creator.name === "string" ? creator.name.trim() : creator.name;
    if (name) return { ...creator, name };

    return {
      ...creator,
      name: `Unknown creator ${getStablePrivateIdentifier(
        String(creator.email || ""),
      )}`,
    };
  });
}
