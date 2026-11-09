import { describe, expect, it } from "vitest";

import { DEMO_CATALOG, DEMO_PROVIDERS } from "../test-fixtures/demoCatalog";
import { GPT_4O_LOGS, fakeRequestLogs } from "../test-fixtures/requestLogs";
import { buildCatalogRows } from "../catalogRows";
import { orgServedRow } from "../orgServed";
import { modelRoutes } from "./modelFacts";
import {
  healthSince,
  percentilePage,
  readRouteHealth,
  routeLogFilter,
} from "./routeHealth";

const MANAGED_ON = { available: true, enabled: true, markup_percent: 5 };

const rowFor = (id) =>
  buildCatalogRows(DEMO_CATALOG.models, {
    providers: DEMO_PROVIDERS.providers,
    managedStatus: MANAGED_ON,
  }).find((r) => r.id === id);

describe("route health: which logged requests belong to a route", () => {
  // The managed route is read by its
  // billing mode, so its Open logs link never names the upstream reseller.
  it("reads a managed route as the catalog id's managed requests", () => {
    const row = rowFor("openai/gpt-4o");
    const routes = modelRoutes(row);
    const managed = routes.find((r) => r.managed);
    expect(routeLogFilter(managed, { modelId: row.id, routes })).toEqual({
      model: "openai/gpt-4o",
      billing_mode: "managed",
    });
  });

  it("reads an own-key route as that provider, under its own model name or the catalog id", () => {
    const row = rowFor("openai/gpt-4o");
    const routes = modelRoutes(row);
    const own = routes.find((r) => !r.managed);
    expect(routeLogFilter(own, { modelId: row.id, routes })).toEqual({
      model: "gpt-4o,openai/gpt-4o",
      provider: "openai",
    });
  });

  it("never counts one request under two routes when an own provider is the managed upstream", () => {
    const managed = {
      key: "managed",
      managed: true,
      provider: "openrouter",
      sendAs: "openrouter/auto",
    };
    const own = {
      key: "own:openrouter",
      managed: false,
      provider: "openrouter",
      sendAs: "auto",
    };
    const routes = [managed, own];
    expect(routeLogFilter(own, { modelId: "openrouter/auto", routes })).toEqual(
      { model: "auto", provider: "openrouter" },
    );
  });

  // A model only the org's provider serves is called by its page id, which
  // names the provider; a caller may send the provider's own model name.
  it("reads a model only the org's provider serves under the page id and the provider's model name", () => {
    const row = orgServedRow("my-vllm/qwen-chat", [
      { id: "my-vllm", name: "my-vllm", models: ["qwen-chat"], pushed: true },
    ]);
    const routes = modelRoutes(row);
    expect(routes).toHaveLength(1);
    expect(routeLogFilter(routes[0], { modelId: row.id, routes })).toEqual({
      model: "my-vllm/qwen-chat,qwen-chat",
      provider: "my-vllm",
    });
  });
});

describe("route health: nearest-rank percentiles by page", () => {
  it("picks the page of the p-th request when sorted by latency", () => {
    expect(percentilePage(6, 0.5)).toBe(3);
    expect(percentilePage(6, 0.95)).toBe(6);
    expect(percentilePage(5, 0.5)).toBe(3);
    expect(percentilePage(1, 0.95)).toBe(1);
    expect(percentilePage(0, 0.5)).toBeNull();
  });
});

describe("route health: one route's last 24 hours", () => {
  const since = healthSince(Date.now());
  const get = (params) => fakeRequestLogs(GPT_4O_LOGS)({ params });

  it("counts only this route's requests, not the model's other providers", async () => {
    const row = rowFor("openai/gpt-4o");
    const routes = modelRoutes(row);
    const own = routes.find((r) => !r.managed);
    const health = await readRouteHealth(
      get,
      routeLogFilter(own, { modelId: row.id, routes }),
      since,
    );
    expect(health).toEqual({
      requests: 5,
      errors: 1,
      errorRate: 20,
      p50: 120,
      p95: 400,
    });
  });

  it("reads the managed route on its own", async () => {
    const row = rowFor("openai/gpt-4o");
    const routes = modelRoutes(row);
    const managed = routes.find((r) => r.managed);
    expect(
      await readRouteHealth(
        get,
        routeLogFilter(managed, { modelId: row.id, routes }),
        since,
      ),
    ).toEqual({ requests: 6, errors: 0, errorRate: 0, p50: 8, p95: 32 });
  });

  it("asks only for the count when a route had no requests", async () => {
    const calls = [];
    const health = await readRouteHealth(
      (params) => {
        calls.push(params);
        return get(params);
      },
      { model: "anthropic/claude-haiku-5.5", provider: "openrouter" },
      since,
    );
    expect(health).toEqual({
      requests: 0,
      errors: 0,
      errorRate: 0,
      p50: null,
      p95: null,
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ limit: 1, started_after: since });
  });

  it("shows no percentile, not a wrong one, when the count moved under it", async () => {
    let first = true;
    const health = await readRouteHealth(
      (params) => {
        if (first) {
          first = false;
          return { count: 50, results: [] };
        }
        return get(params);
      },
      { model: "gpt-4o", provider: "openai" },
      since,
    );
    expect(health.requests).toBe(50);
    expect(health.p95).toBeNull();
  });

  it("starts the window 24 hours back, on a whole minute", () => {
    const now = Date.parse("2026-10-20T12:34:56.789Z");
    expect(healthSince(now)).toBe("2026-10-19T12:34:00.000Z");
  });
});
