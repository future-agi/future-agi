const DEFAULT_BLOCKED_PATH_PREFIXES = [
  "/dashboard/observe",
  "/dashboard/users",
  "/dashboard/dashboards",
  "/dashboard/tasks",
  "/dashboard/annotations",
  "/dashboard/error-feed",
];

const runtimeBlockedPrefixes =
  typeof window === "undefined"
    ? undefined
    : window.__FUTURE_AGI_CONFIG__?.VITE_SESSION_REPLAY_BLOCKED_PATH_PREFIXES;
const configuredBlockedPrefixes =
  runtimeBlockedPrefixes !== undefined &&
  String(runtimeBlockedPrefixes).trim() !== ""
    ? runtimeBlockedPrefixes
    : import.meta.env.VITE_SESSION_REPLAY_BLOCKED_PATH_PREFIXES;

export const SESSION_REPLAY_BLOCKED_PATH_PREFIXES = configuredBlockedPrefixes
  ? configuredBlockedPrefixes
      .split(",")
      .map((prefix) => prefix.trim())
      .filter(Boolean)
  : DEFAULT_BLOCKED_PATH_PREFIXES;

const normalizePathname = (pathname) => {
  if (typeof pathname !== "string" || pathname.length === 0) return "/";
  return pathname.split(/[?#]/, 1)[0].replace(/\/$/, "") || "/";
};

export const isSessionReplayBlockedPath = (pathname) => {
  const normalizedPathname = normalizePathname(pathname);

  return SESSION_REPLAY_BLOCKED_PATH_PREFIXES.some((prefix) => {
    const normalizedPrefix = normalizePathname(prefix);
    return (
      normalizedPathname === normalizedPrefix ||
      normalizedPathname.startsWith(`${normalizedPrefix}/`)
    );
  });
};

const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export const SESSION_REPLAY_URL_BLOCKLIST =
  SESSION_REPLAY_BLOCKED_PATH_PREFIXES.map((prefix) => ({
    url: `${escapeRegExp(normalizePathname(prefix))}(?:[/?#]|$)`,
    matching: "regex",
  }));
