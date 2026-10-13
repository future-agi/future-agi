/**
 * Customer-facing integration discovery is a projection of the remote catalog.
 * This file contains labels and matching only; it never asserts that a provider
 * is connected or creates a connector row that the server did not return.
 */
export const INTEGRATION_COVERAGE = [
  {
    id: "google-drive",
    family: "Google",
    label: "Google Drive",
    aliases: ["google-drive", "gdrive", "drive"],
  },
  {
    id: "gmail",
    family: "Google",
    label: "Gmail",
    aliases: ["gmail", "google-mail"],
  },
  {
    id: "google-calendar",
    family: "Google",
    label: "Google Calendar",
    aliases: ["google-calendar", "gcalendar", "calendar"],
  },
  {
    id: "google-sheets",
    family: "Google",
    label: "Google Sheets",
    aliases: ["google-sheets", "gsheets", "sheets"],
  },
  {
    id: "outlook",
    family: "Microsoft",
    label: "Outlook",
    aliases: ["outlook", "microsoft-outlook"],
  },
  {
    id: "onedrive",
    family: "Microsoft",
    label: "OneDrive",
    aliases: ["onedrive", "one-drive"],
  },
  {
    id: "teams",
    family: "Microsoft",
    label: "Microsoft Teams",
    aliases: ["teams", "microsoft-teams"],
  },
  {
    id: "sharepoint",
    family: "Microsoft",
    label: "SharePoint",
    aliases: ["sharepoint", "microsoft-sharepoint"],
  },
  { id: "slack", family: "Other", label: "Slack", aliases: ["slack"] },
  { id: "github", family: "Other", label: "GitHub", aliases: ["github"] },
  { id: "notion", family: "Other", label: "Notion", aliases: ["notion"] },
  { id: "linear", family: "Other", label: "Linear", aliases: ["linear"] },
  { id: "stripe", family: "Other", label: "Stripe", aliases: ["stripe"] },
  { id: "hubspot", family: "Other", label: "HubSpot", aliases: ["hubspot"] },
  {
    id: "salesforce",
    family: "Other",
    label: "Salesforce",
    aliases: ["salesforce"],
  },
  {
    id: "custom-mcp",
    family: "Other",
    label: "Custom MCP",
    aliases: ["custom-mcp", "mcp", "model-context-protocol"],
  },
  {
    id: "remote-execution",
    family: "Other",
    label: "Remote execution",
    aliases: [
      "remote-execution",
      "remote",
      "daytona",
      "e2b",
      "kubernetes-jobs",
    ],
  },
];

const CONNECTED_STATES = new Set([
  "connected",
  "installed",
  "active",
  "ready",
  "authorized",
]);
const UNAVAILABLE_STATES = new Set([
  "unavailable",
  "unsupported",
  "disabled",
  "error",
]);
const valueText = (value) =>
  typeof value === "string" ? value.trim().toLowerCase() : "";

export function integrationState(row) {
  if (!row || typeof row !== "object") return "unavailable";
  if (row.connected === true || row.authorized === true) return "connected";
  const state = valueText(row.status || row.state || row.connectionState);
  if (CONNECTED_STATES.has(state)) return "connected";
  if (row.available === false || UNAVAILABLE_STATES.has(state))
    return "unavailable";
  return "available";
}

export function integrationFamily(row) {
  const raw = valueText(
    row?.family || row?.category || row?.providerCategory || row?.provider,
  );
  if (
    [
      "mcp",
      "custom-mcp",
      "model-context-protocol",
      "remote",
      "remote-execution",
    ].includes(raw)
  )
    return "Other";
  const known = INTEGRATION_COVERAGE.find(
    (item) => item.family.toLowerCase() === raw,
  );
  return (
    known?.family ||
    (raw
      ? raw
          .replace(/[-_]+/g, " ")
          .replace(/\b\w/g, (letter) => letter.toUpperCase())
      : "Other")
  );
}

function rowSearchText(row) {
  return [
    row?.id,
    row?.connectorId,
    row?.name,
    row?.label,
    row?.provider,
    row?.description,
    integrationFamily(row),
  ]
    .filter((value) => typeof value === "string")
    .join(" ")
    .toLowerCase();
}

export function filterIntegrations(rows, { query = "", filter = "all" } = {}) {
  const normalizedQuery = valueText(query);
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    const state = integrationState(row);
    const family = integrationFamily(row);
    const stateMatch =
      filter === "all" || filter === state || filter === family;
    return (
      stateMatch &&
      (!normalizedQuery || rowSearchText(row).includes(normalizedQuery))
    );
  });
}

function rowMatchesCoverage(row, item) {
  const haystack = rowSearchText(row);
  return item.aliases.some((alias) => haystack.includes(alias));
}

export function coverageRows(rows) {
  const available = Array.isArray(rows) ? rows : [];
  return INTEGRATION_COVERAGE.map((item) => {
    const matching = available.filter((row) => rowMatchesCoverage(row, item));
    const connected = matching.some(
      (row) => integrationState(row) === "connected",
    );
    return {
      ...item,
      state: connected
        ? "connected"
        : matching.length > 0
          ? "available"
          : "unavailable",
      serverRows: matching,
    };
  });
}
