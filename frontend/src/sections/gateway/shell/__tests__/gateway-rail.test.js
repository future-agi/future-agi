import { describe, expect, it } from "vitest";

import { paths } from "src/routes/paths";

import { activeRailPath, gatewayRailGroups } from "../gateway-rail";
import { GATEWAY_ICON_NAMES } from "../gateway-icons";

const gw = paths.dashboard.gateway;
const itemsOf = (groups) => groups.flatMap((group) => group.items);
const pathsOf = (groups) => itemsOf(groups).map((item) => item.path);

describe("gateway rail", () => {
  const groups = gatewayRailGroups({ integrations: true });

  // Policies is hidden (page-status.js), so its group is named for the one
  // page it shows. Integrations and MCP tools sit with Providers, so the rail
  // keeps six groups and fits a 1440x960 screen (GatewayRail.fit.test.jsx).
  it("has the six groups in order", () => {
    expect(groups.map((group) => group.subheader ?? null)).toEqual([
      null,
      "Models",
      "Providers, Tools & Keys",
      "Guardrails",
      "Observability",
      "Settings",
    ]);
    expect(groups[0].items.map((item) => item.title)).toEqual(["Overview"]);
  });

  it("puts Integrations next to Providers, then MCP tools", () => {
    const providers = groups.find((group) => group.id === "providers");
    expect(providers.items.map((item) => [item.title, item.path])).toEqual([
      ["Providers", gw.providers],
      ["Integrations", gw.integrations],
      ["MCP tools", gw.mcp],
      ["API keys", gw.keys],
      ["Fallbacks", gw.fallbacks],
    ]);
    expect(gw.integrations).toBe("/dashboard/gateway/integrations");
  });

  it("splits models into the catalog and auto routing", () => {
    const models = groups.find((group) => group.subheader === "Models");
    expect(models.items.map((item) => [item.title, item.path])).toEqual([
      ["Models", gw.models],
      ["Auto routing", `${gw.models}/auto`],
    ]);
  });

  it.each([true, false, undefined])(
    "keeps Models and Auto routing reachable without promising managed access (noManagedModels=%s)",
    (noManagedModels) => {
      const items = itemsOf(gatewayRailGroups({ noManagedModels }));
      expect(items.find((item) => item.path === gw.models)).toMatchObject({
        title: "Models",
      });
      expect(
        items.find((item) => item.path === gw.models).managedOnly,
      ).not.toBe(true);
      expect(items.map((item) => item.path)).toContain(`${gw.models}/auto`);
      expect(activeRailPath(`${gw.models}/own/model`, items)).toBe(gw.models);
      expect(activeRailPath(`${gw.models}/auto`, items)).toBe(
        `${gw.models}/auto`,
      );
    },
  );

  it("puts logs, analytics, monitoring and alerts first under observability", () => {
    const observability = groups.find(
      (group) => group.subheader === "Observability",
    );
    expect(
      observability.items.slice(0, 4).map((item) => [item.title, item.path]),
    ).toEqual([
      ["Logs", gw.logs],
      ["Analytics", gw.analytics],
      ["Monitoring", gw.monitoring],
      ["Alerts", `${gw.monitoring}/rules`],
    ]);
  });

  it("keeps every page that works reachable", () => {
    const reachable = pathsOf(groups);
    for (const path of [
      gw.overview,
      gw.models,
      gw.providers,
      gw.integrations,
      gw.mcp,
      gw.keys,
      gw.fallbacks,
      gw.guardrails.root,
      gw.logs,
      gw.analytics,
      gw.monitoring,
      gw.sessions,
      gw.runs,
      gw.customProperties,
      gw.webhooks,
      gw.budgets,
      gw.settings,
    ]) {
      expect(reachable).toContain(path);
    }
  });

  it("lists no page that is hidden or moved", () => {
    const reachable = pathsOf(groups);
    for (const path of [
      gw.playground,
      gw.capabilities,
      gw.policies.root,
      gw.billing.root,
    ]) {
      expect(reachable.some((p) => p.startsWith(path))).toBe(false);
    }
  });

  it("gives every item a rail icon, never an Iconify name", () => {
    for (const item of itemsOf(groups)) {
      expect(item.iconName).toBeUndefined();
      expect(GATEWAY_ICON_NAMES).toContain(item.icon);
    }
  });

  it("uses each path once", () => {
    const all = pathsOf(groups);
    expect(new Set(all).size).toBe(all.length);
  });
});

describe("active rail item", () => {
  const items = itemsOf(gatewayRailGroups({ integrations: true }));
  const titleFor = (pathname) =>
    items.find((item) => item.path === activeRailPath(pathname, items))
      ?.title ?? null;

  it("marks Overview only on the overview itself", () => {
    expect(titleFor("/dashboard/gateway")).toBe("Overview");
    expect(titleFor("/dashboard/gateway/")).toBe("Overview");
    expect(titleFor("/dashboard/gateway/logs")).toBe("Logs");
    expect(titleFor("/dashboard/gateway/unknown")).toBe(null);
  });

  it("prefers the longest matching item", () => {
    expect(titleFor("/dashboard/gateway/models")).toBe("Models");
    expect(titleFor("/dashboard/gateway/models/auto")).toBe("Auto routing");
    expect(titleFor("/dashboard/gateway/monitoring/metrics")).toBe(
      "Monitoring",
    );
    expect(titleFor("/dashboard/gateway/monitoring/rules")).toBe("Alerts");
    expect(titleFor("/dashboard/gateway/guardrails/logs")).toBe("Guardrails");
    expect(titleFor("/dashboard/gateway/integrations")).toBe("Integrations");
    expect(titleFor("/dashboard/gateway/mcp")).toBe("MCP tools");
    expect(titleFor("/dashboard/gateway/mcp/servers")).toBe("MCP tools");
  });

  it("marks Alerts on both of its tabs, also Channels under /monitoring", () => {
    expect(titleFor("/dashboard/gateway/monitoring/channels")).toBe("Alerts");
    expect(titleFor("/dashboard/gateway/monitoring/channels/")).toBe("Alerts");
    expect(titleFor("/dashboard/gateway/monitoring")).toBe("Monitoring");
  });

  it("does not match a path that only shares a prefix", () => {
    expect(titleFor("/dashboard/gateway/logsx")).toBe(null);
  });
});

describe("W07 catalogue navigation", () => {
  it.each([false, undefined, null, 0])(
    "OFF=%s retains legacy MCP tools",
    (integrations) => {
      const reachable = pathsOf(gatewayRailGroups({ integrations }));
      expect(reachable).not.toContain(gw.integrations);
      expect(reachable).toContain(gw.mcp);
      expect(reachable).toContain(gw.providers);
    },
  );
  it("ON lists Integrations for catalogue or separately enabled accounts", () => {
    expect(pathsOf(gatewayRailGroups({ integrations: true }))).toContain(
      gw.integrations,
    );
  });
});
