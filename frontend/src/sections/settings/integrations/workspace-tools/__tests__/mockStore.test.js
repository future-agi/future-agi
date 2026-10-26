import { beforeEach, describe, expect, it, vi } from "vitest";

const KEY = "fagi.workspaceTools.v1";

const loadStore = async () => {
  vi.resetModules();
  return import("../mockStore");
};

describe("workspace tools mock store", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("starts with nothing connected", async () => {
    const store = await loadStore();
    const s = store.getIntegrations();
    expect(s.github).toBeNull();
    expect(store.connectedCodingAgent(s)).toBeNull();
    expect(store.connectedCloudAgents(s)).toHaveLength(0);
  });

  it("seeds sample repos, connected agents and PRs when GitHub is installed", async () => {
    const store = await loadStore();
    store.installGithub({ account: "acme-inc", access: "all" });
    const s = store.getIntegrations();
    expect(store.installedRepos(s).length).toBeGreaterThan(0);
    expect(s.connectedAgents).toHaveLength(2);
    expect(s.prs).toHaveLength(2);
    expect(store.githubAccountsLabel(s)).toBe("acme-inc");
  });

  it("removes sample rows again when the last installation is uninstalled", async () => {
    const store = await loadStore();
    store.installGithub({ account: "acme-inc", access: "all" });
    store.uninstallGithub("acme-inc");
    const s = store.getIntegrations();
    expect(s.github).toBeNull();
    expect(s.connectedAgents).toHaveLength(0);
    expect(s.prs).toHaveLength(0);
  });

  it("connects cloud agents side by side and seeds recent hand-offs", async () => {
    const store = await loadStore();
    store.connectCloudAgent("codex_cloud", {
      keyRef: "vault://acme/coding-agents/codex_cloud_api_key",
    });
    let s = store.getIntegrations();
    expect(store.connectedCloudAgents(s).map((a) => a.id)).toEqual([
      "codex_cloud",
    ]);
    expect(s.briefHandoffs).toHaveLength(2);
    store.connectCloudAgent("claude_code_web", {
      keyRef: "vault://acme/coding-agents/claude_code_web_api_key",
    });
    s = store.getIntegrations();
    expect(store.connectedCloudAgents(s).map((a) => a.id)).toEqual([
      "claude_code_web",
      "codex_cloud",
    ]);
    expect(s.briefHandoffs).toHaveLength(2);
  });

  it("routes Slack events to the default channel unless they are off by default", async () => {
    const store = await loadStore();
    const rules = store.slackRulesOf(store.getIntegrations(), "#agent-quality");
    expect(rules.run_finished).toBe("#agent-quality");
    expect(rules.version).toBe("#agent-quality");
    expect(rules.error_feed).toBeNull();
    expect(rules.alert).toBeNull();
    store.setSlackRule("alert", "#eng-alerts");
    expect(
      store.slackRulesOf(store.getIntegrations(), "#agent-quality").alert,
    ).toBe("#eng-alerts");
  });

  it("has sample Slack messages to show", async () => {
    const store = await loadStore();
    expect(store.getIntegrations().slackLog.length).toBe(3);
  });

  it("keeps each organization's mock connections apart", async () => {
    const store = await loadStore();
    store.setIntegrationsScope("org-a");
    store.installGithub({ account: "acme-inc", access: "all" });
    store.setIntegrationsScope("org-b");
    expect(store.getIntegrations().github).toBeNull();
    store.setIntegrationsScope("org-a");
    expect(store.getIntegrations().github).not.toBeNull();
    expect(window.localStorage.getItem(`${KEY}:org-a`)).toContain("acme-inc");
  });

  it("persists under its own storage key", async () => {
    const store = await loadStore();
    store.connectCodingAgent("claude_code", "plugin");
    expect(
      JSON.parse(window.localStorage.getItem(KEY)).codingAgents.claude_code.via,
    ).toBe("plugin");
  });

  it("falls back to an empty store when saved data is corrupt", async () => {
    window.localStorage.setItem(KEY, "{not json");
    const store = await loadStore();
    expect(store.getIntegrations().github).toBeNull();
  });
});
