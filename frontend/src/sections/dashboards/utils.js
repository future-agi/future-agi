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
  return db?.created_by?.name || "";
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
  const addUser = (u, time) => {
    if (!u || !u.email || seen.has(u.email)) return;
    seen.add(u.email);
    users.push({ ...u, displayName: u.name || "Unknown user", time });
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

/**
 * Label the creator filter entries, giving unnamed creators a stable suffix.
 *
 * The suffix is derived from the creator's position after sorting by email, not
 * from list iteration order, so a given person keeps the same "Unknown creator
 * N" label across refetches, renames and re-sorts. The filter stores `email`,
 * so an order-dependent suffix would silently renumber the selected filter's
 * label underneath the user.
 */
export function labelCreatorsWithStableUnknownIndex(entries) {
  const unnamedEmails = entries
    .filter((creator) => !creator.name)
    .map((creator) => creator.email)
    .sort();

  const unnamedIndexByEmail = new Map(
    unnamedEmails.map((email, index) => [email, index + 1]),
  );

  return entries.map((creator) => {
    if (creator.name) return creator;

    return {
      ...creator,
      name:
        unnamedEmails.length > 1
          ? `Unknown creator ${unnamedIndexByEmail.get(creator.email)}`
          : "Unknown creator",
    };
  });
}
