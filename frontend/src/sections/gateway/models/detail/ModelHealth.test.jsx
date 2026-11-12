import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { onlineManager } from "@tanstack/react-query";

import { endpoints } from "src/utils/axios";

import { contractFailures, renderModelsAt } from "../test-fixtures/harness";
import ModelHealth from "./ModelHealth";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, ...api } };
});
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../../mcp/testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});

const ROUTES = [
  { key: "managed", label: "Future AGI", managed: true, sendAs: "openai/gpt-4o" },
  { key: "own:openai", label: "OpenAI", provider: "openai", sendAs: "gpt-4o" },
];
const FIRST = "2026-11-09T10:00:12.345Z";
const NEXT = "2026-11-09T10:05:37.456Z";
const group = (label) => screen.getByRole("group", { name: `${label} traffic status` });
const checkedAt = (label) => group(label).querySelector("time")?.getAttribute("datetime");
let client;
const open = (routes = ROUTES) => {
  const view = renderModelsAt(
    "/dashboard/gateway/models/openai/gpt-4o",
    <ModelHealth routes={routes} modelId="openai/gpt-4o" />,
  );
  client = view.client;
  return view;
};
function response(params) {
  return {
    data: {
      count: params.is_error ? 1 : 5,
      results: params.ordering ? [{ latency_ms: params.page === 3 ? 120 : 400 }] : [],
    },
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(FIRST));
  api.get.mockReset();
  api.post.mockReset();
  api.get.mockImplementation(async (_url, { params }) => response(params));
});

afterEach(async () => {
  cleanup();
  client?.clear();
  onlineManager.setOnline(true);
  vi.useRealTimers();
  expect(api.post).not.toHaveBeenCalled();
  expect(api.get.mock.calls.every(([url]) => url === endpoints.gateway.requestLogs)).toBe(true);
  expect(await contractFailures(api)).toEqual([]);
});

describe("model traffic recovery", () => {
  it("keeps the successful route visible beside a read failure and retries only that route", async () => {
    let fail = true;
    api.get.mockImplementation(async (_url, { params }) => {
      if (params.provider && fail) throw new Error("read failed");
      return response(params);
    });
    open();
    const row = await screen.findByRole("row", { name: /^Future AGI / });
    expect(row).toHaveTextContent("5");
    expect(row).toHaveTextContent("20%");
    expect(row).toHaveTextContent("120 ms");
    expect(row).toHaveTextContent("400 ms");
    await waitFor(() => expect(group("OpenAI")).toHaveTextContent("Read failed"));
    expect(group("Future AGI")).toHaveTextContent("Last checked");
    expect(checkedAt("OpenAI")).toBeUndefined();
    api.get.mockClear();
    fail = false;
    await userEvent.click(within(group("OpenAI")).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("row", { name: /^OpenAI / })).toHaveTextContent("120 ms");
    expect(api.get).toHaveBeenCalledTimes(4);
    expect(api.get.mock.calls.every(([, { params }]) => params.provider === "openai")).toBe(true);
    expect(group("OpenAI")).not.toHaveTextContent("Read failed");
  });

  it("distinguishes a successful zero count from a failed read", async () => {
    api.get.mockImplementation(async (_url, { params }) => {
      if (params.provider) throw new Error("read failed");
      return { data: { count: 0, results: [] } };
    });
    open();
    await waitFor(() => expect(group("Future AGI")).toHaveTextContent("No requests"));
    expect(group("Future AGI")).not.toHaveTextContent("Read failed");
    expect(checkedAt("Future AGI")).toBe(FIRST);
    await waitFor(() => expect(group("OpenAI")).toHaveTextContent("Read failed"));
    expect(group("OpenAI")).not.toHaveTextContent("No requests");
    expect(api.get.mock.calls.filter(([, { params }]) => !params.provider)).toHaveLength(1);
  });

  it("shows a pending route without hiding a successful route", async () => {
    api.get.mockImplementation((_url, { params }) =>
      params.provider ? new Promise(() => {}) : Promise.resolve(response(params)),
    );
    open();
    expect(await screen.findByRole("row", { name: /^Future AGI / })).toHaveTextContent("400 ms");
    expect(group("OpenAI")).toHaveTextContent("Loading");
    expect(screen.getByRole("button", { name: /Refresh all/ })).toBeDisabled();
  });

  it("refreshes all routes with shared bounds and labels retained metrics stale until successful recovery", async () => {
    open();
    await screen.findByRole("row", { name: /^OpenAI / });
    await waitFor(() => expect(screen.getByRole("button", { name: /Refresh all/ })).toBeEnabled());
    expect(checkedAt("OpenAI")).toBe(FIRST);
    api.get.mockClear();
    vi.setSystemTime(new Date(NEXT));
    api.get.mockImplementation(async (_url, { params }) => {
      if (params.provider) throw new Error("read failed");
      return response(params);
    });
    await userEvent.click(screen.getByRole("button", { name: /Refresh all/ }));
    await waitFor(() => expect(group("OpenAI")).toHaveTextContent("Stale"));
    await waitFor(() => expect(group("OpenAI")).toHaveTextContent("Read failed"));
    expect(screen.getByRole("row", { name: /^OpenAI / })).toHaveTextContent("400 ms");
    expect(checkedAt("OpenAI")).toBe(FIRST);
    await waitFor(() => expect(checkedAt("Future AGI")).toBe(NEXT));
    expect(api.get).toHaveBeenCalledTimes(5);
    for (const [, { params }] of api.get.mock.calls) {
      expect(params).toMatchObject({
        started_after: "2026-11-08T10:05:37.456Z", started_before: NEXT,
      });
    }
    api.get.mockImplementation(async (_url, { params }) => response(params));
    vi.setSystemTime(new Date("2026-11-09T10:06:00.000Z"));
    await userEvent.click(within(group("OpenAI")).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(group("OpenAI")).not.toHaveTextContent("Stale"));
    expect(checkedAt("OpenAI")).toBe("2026-11-09T10:06:00.000Z");
  });

  it("preserves qualified Logs links and describes organization-local total request latency without probes", async () => {
    open();
    const managed = await screen.findByRole("row", { name: /^Future AGI / });
    const own = await screen.findByRole("row", { name: /^OpenAI / });
    const query = (row) => Object.fromEntries(new URL(
      within(row).getByRole("link", { name: "Open logs" }).href,
    ).searchParams);
    expect(query(managed)).toEqual({ model: "openai/gpt-4o", billing_mode: "managed" });
    expect(query(own)).toEqual({ model: "gpt-4o,openai/gpt-4o", provider: "openai" });
    expect(screen.getByRole("region", { name: "Live health" })).toHaveTextContent("your organization");
    expect(screen.getByText(/p50 and p95 are total request latency/)).toBeInTheDocument();
    expect(screen.queryByText(/healthy|global uptime|TTFT/i)).toBeNull();
    expect(api.get).toHaveBeenCalledTimes(8);
  });

  it("keeps a qualified Logs link for a failed or empty route", async () => {
    api.get.mockImplementation(async (_url, { params }) => {
      if (params.provider) throw new Error("read failed");
      return { data: { count: 0, results: [] } };
    });
    open();
    await waitFor(() => expect(group("OpenAI")).toHaveTextContent("Read failed"));
    for (const label of ["Future AGI", "OpenAI"]) {
      const link = within(group(label)).getByRole("link", { name: "Open logs" });
      const url = new URL(link.href);
      expect(url.pathname).toBe("/dashboard/gateway/logs");
      expect(url.searchParams.get(label === "OpenAI" ? "provider" : "billing_mode"))
        .toBe(label === "OpenAI" ? "openai" : "managed");
    }
  });

  it("does not claim traffic loaded while a first read is paused offline", () => {
    onlineManager.setOnline(false);
    open([ROUTES[0]]);
    expect(group("Future AGI")).toHaveTextContent("Waiting for traffic read");
    expect(group("Future AGI")).not.toHaveTextContent("Traffic loaded");
    expect(group("Future AGI")).not.toHaveTextContent("No requests");
    expect(checkedAt("Future AGI")).toBeUndefined();
    expect(api.get).not.toHaveBeenCalled();
  });

  it("does not read or offer Refresh all when there are no routes", () => {
    open([]);
    expect(screen.getByText("No route reaches this model, so it has no traffic.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Refresh all/ })).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
  });
});
