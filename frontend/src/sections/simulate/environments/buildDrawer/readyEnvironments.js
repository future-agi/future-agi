import { useMemo } from "react";
import { useHarnessReleases } from "src/api/harness/releases";

/*
  Ready environments: the sealed environment releases this workspace may use, read from
  the release API (environment-releases.md §7.6). Each release carries a catalogue block
  sealed from its package (title, first sentence, seeded records, attached apps, example
  requests); a release sealed before that block existed shows its name and its tools.
*/

const TYPE_LABEL = { voice: "Voice", chat: "Chat" };

// Gmail, Drive, Slack and Calendar capabilities are named for the official MCP server they
// mirror; the card names the app.
const APP_LABEL = {
  gmail_inbox_triage: "Gmail",
  drive_policy_search: "Google Drive",
  slack_channel_digest: "Slack",
  calendar_scheduling_desk: "Google Calendar",
};
export const appLabelOf = (app) =>
  APP_LABEL[app.capability] ||
  String(app.server || app.capability).replace(/ MCP server.*$/, "");

const titleOf = (name) =>
  name.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

// The suite a ready environment is used with: its base suite, the one sealed with it.
const baseSuiteOf = (suites = []) =>
  suites.find((s) => s.name.endsWith("_base")) ||
  suites.find((s) => s.origins && s.origins.base === s.scenario_count) ||
  suites[0] ||
  null;

/** One release as the drawer's cards, browser and panel read it; null without a suite. */
export function readyEnvironmentFrom(release) {
  const suite = baseSuiteOf(release.suites);
  if (!suite) return null;
  const catalogue = release.catalogue || null;
  const type = catalogue?.channel === "voice" ? "voice" : "chat";
  const records = catalogue?.records || {};
  const apps = catalogue?.apps || [];
  return {
    id: release.id,
    name: catalogue?.title || titleOf(release.name),
    releaseName: release.name,
    digest: release.digest,
    system: release.system,
    tagline: catalogue?.summary || "",
    type,
    typeLabel: TYPE_LABEL[type],
    // The browser filters and groups by domain; a release is its own domain.
    domain: release.name,
    domainLabel: catalogue?.title || titleOf(release.name),
    tools: release.tools.length,
    tables: Object.keys(records).length,
    toolList: release.tools,
    rules: release.policy?.agent_rules || [],
    parameters: release.policy?.parameters || [],
    records,
    recordCount: Object.values(records).reduce((n, c) => n + c, 0),
    apps: apps.map((app) => ({ ...app, label: appLabelOf(app) })),
    exampleRequests: catalogue?.example_requests || [],
    suite,
    scenarioCount: suite.scenario_count,
    popularity: 0,
    release,
  };
}

/** The ready environments, system releases first, then by name. */
export function useReadyEnvironments({ enabled = true } = {}) {
  const query = useHarnessReleases({ enabled });
  const worlds = useMemo(
    () =>
      (query.data || [])
        .map(readyEnvironmentFrom)
        .filter(Boolean)
        .sort(
          (a, b) =>
            Number(b.system) - Number(a.system) || a.name.localeCompare(b.name),
        ),
    [query.data],
  );
  return {
    worlds,
    isLoading: query.isLoading,
    // Only when there is no list to show: one read before stays up.
    error: query.data ? null : query.error,
    refetch: query.refetch,
  };
}
