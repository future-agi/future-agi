// Public dataset link words and choices (W4-6). Pure. The expiry choices
// and the default come from the workspace policy the server sends
// (`expiry_choices`, `default_expiry_days`); this only keeps them within the
// policy's maximum.

const DAY_MS = 24 * 60 * 60 * 1000;

/** The server's expiry choices in days, whole and within the policy maximum. */
export function expiryChoices(policy) {
  const max = Number(policy?.max_days);
  const list = Array.isArray(policy?.expiry_choices)
    ? policy.expiry_choices
    : [];
  return [
    ...new Set(
      list.map(Number).filter((d) => Number.isInteger(d) && d >= 1 && d <= max),
    ),
  ].sort((a, b) => a - b);
}

export const expiryLabel = (days) => (days === 1 ? "1 day" : `${days} days`);

/** The server's default when it is one of the choices, else the longest. */
export function defaultExpiry(policy) {
  const c = expiryChoices(policy);
  const d = Number(policy?.default_expiry_days);
  return c.includes(d) ? d : c[c.length - 1] ?? null;
}

/** "Wed 29 Oct" in the viewer's time zone. */
export const shortDate = (iso) =>
  new Date(iso)
    .toLocaleDateString("en-GB", {
      weekday: "short",
      day: "numeric",
      month: "short",
    })
    .replace(",", "");

export const publicUrl = (origin, link) => `${origin}${link.url_path}`;

/** "the rows", "the rows and experiments", "the rows, experiments and human feedback". */
const sharedParts = (include = {}) => {
  const parts = [
    include.rows !== false ? "rows" : null,
    include.experiments ? "experiments" : null,
    include.feedback ? "human feedback" : null,
  ].filter(Boolean);
  if (parts.length <= 1) return `the ${parts[0] ?? "rows"}`;
  return `the ${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
};

/** What the link shows, of which version, to whom, until when. */
const expiresWords = (link) =>
  `Expires ${shortDate(link.expires_at)}${link.expires_at_requested ? " (the workspace's longest)" : ""}.`;

export const anyoneLine = (link) => {
  const of =
    link.version === "latest"
      ? "the latest version"
      : `v${link.version_number}`;
  return `Anyone with the link can see ${sharedParts(link.include)} of ${of}, without signing in. ${expiresWords(link)}`;
};

/** An active link while the workspace has public links off. */
export const pausedLine = (link, workspaceName) =>
  `This link doesn't open while public links are off${workspaceName ? ` for ${workspaceName}` : ""}. Turning them on reopens it. ${expiresWords(link)}`;

/** "Opened 3 times · last 2 h ago" or "Not opened yet". */
export function viewsLine(link, now = Date.now()) {
  if (!link.views) return "Not opened yet";
  const times = link.views === 1 ? "once" : `${link.views} times`;
  if (!link.last_opened_at) return `Opened ${times}`;
  const mins = Math.max(
    0,
    Math.round((now - Date.parse(link.last_opened_at)) / 60000),
  );
  const ago =
    mins < 1
      ? "just now"
      : mins < 60
        ? `${mins} min ago`
        : mins < 60 * 24
          ? `${Math.round(mins / 60)} h ago`
          : `${Math.round(mins / (60 * 24))} d ago`;
  return `Opened ${times} · last ${ago}`;
}

export const includeLabels = (include = {}) =>
  [
    include.rows !== false ? "Rows" : null,
    include.experiments ? "Experiments and run results" : null,
    include.feedback ? "Human feedback" : null,
  ].filter(Boolean);

export const versionLabel = (link) =>
  link.version === "latest"
    ? "Always the latest version"
    : `v${link.version_number}, frozen`;

/** A link's past state for the "Earlier links" list. */
export function earlierLine(link) {
  const when = link.revoked_at
    ? `Turned off ${shortDate(link.revoked_at)}`
    : `Expired ${shortDate(link.expires_at)}`;
  const opened = link.views ? `opened ${link.views}×` : "not opened";
  return `${versionLabel(link)} · ${includeLabels(link.include).join(", ")} · ${when} · ${opened}`;
}

export const isActive = (link, now = Date.now()) =>
  !link.revoked_at && Date.parse(link.expires_at) > now;

export const DAY = DAY_MS;
