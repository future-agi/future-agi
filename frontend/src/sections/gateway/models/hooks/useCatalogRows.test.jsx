import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { endpoints } from "src/utils/axios";
import {
  answerFrom,
  contractFailures,
  demoResponses,
} from "../test-fixtures/harness";
import { useCatalogRows } from "./useCatalogRows";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const context = vi.hoisted(() => ({
  gatewayId: "default",
  organizationId: "org-a",
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: context.gatewayId }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: context.organizationId }),
}));

const gw = endpoints.gateway;
const open = (overrides = {}) => {
  const responses = demoResponses(endpoints, overrides);
  api.get.mockImplementation(answerFrom(responses));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const hook = renderHook(() => useCatalogRows(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  return { ...hook, client, responses };
};
const embedding = (result) =>
  result.current.rows?.find(
    (row) => row.id === "openai/text-embedding-3-small",
  );

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  context.gatewayId = "default";
  context.organizationId = "org-a";
});

afterEach(async () => {
  expect(await contractFailures(api)).toEqual([]);
  expect(api.post).not.toHaveBeenCalled();
});

describe("catalog facts and independent provider access", () => {
  it.each(["pending", "error"])(
    "keeps catalog facts with %s provider access, without inferring absence",
    async (state) => {
      const { result } = open({
        [gw.providers("default")]:
          state === "pending"
            ? () => new Promise(() => {})
            : new Error("Provider read failed"),
      });
      await waitFor(() =>
        expect(embedding(result)?.providerAccessState).toBe(state),
      );
      expect(embedding(result)).toMatchObject({
        accessLabel: "Access not verified",
        input: null,
        output: null,
      });
      expect(
        result.current.rows.find((row) => row.id === "openai/gpt-4o"),
      ).toMatchObject({ onCredits: true, managedOn: true, access: "managed" });
      expect(result.current.isLoading).toBe(false);
    },
  );

  it("retains own routes on refresh failure and updates access only after Retry succeeds", async () => {
    const { result, responses } = open();
    await waitFor(() =>
      expect(embedding(result)?.providerAccessState).toBe("ready"),
    );
    const routes = embedding(result).ownRoutes;
    responses[gw.providers("default")] = new Error("Refresh failed");
    await act(async () => {
      await result.current.providers.refetch();
    });
    expect(embedding(result)).toMatchObject({
      providerAccessState: "error",
      accessLabel: "Access not verified",
      ownRoutes: routes,
    });
    responses[gw.providers("default")] = {
      status: true,
      result: { providers: [] },
    };
    await act(async () => {
      await result.current.providers.refetch();
    });
    expect(embedding(result)).toMatchObject({
      providerAccessState: "ready",
      access: "none",
      ownRoutes: [],
    });
  });

  it("revalidates legacy cache on remount instead of showing another context's facts", async () => {
    const first = open();
    await waitFor(() =>
      expect(embedding(first.result)?.providerAccessState).toBe("ready"),
    );
    first.unmount();
    context.organizationId = "org-b";
    first.responses[gw.managedModels] = new Error("New context unavailable");
    first.responses[gw.managedModelsCatalog] = new Error(
      "New context unavailable",
    );
    first.responses[gw.providers("default")] = new Error(
      "New context unavailable",
    );
    const next = renderHook(() => useCatalogRows(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={first.client}>
          {children}
        </QueryClientProvider>
      ),
    });
    expect(next.result.current.rows).toBeUndefined();
    expect(next.result.current.providers.data).toBeUndefined();
    await waitFor(() => expect(next.result.current.status.error).toBeTruthy());
    expect(next.result.current.rows).toBeUndefined();
  });

  it("retains validated facts after a malformed refresh, never treating it as an empty catalog", async () => {
    const { result, responses } = open();
    await waitFor(() => expect(result.current.rows?.length).toBeGreaterThan(0));
    const ids = result.current.rows.map((row) => row.id);
    responses[gw.managedModelsCatalog] = { status: true, result: {} };
    await act(async () => {
      await result.current.catalog.refetch();
    });
    expect(result.current.rows.map((row) => row.id)).toEqual(ids);
    expect(result.current.catalog.error).toBeTruthy();
  });

  it.each(["gateway", "organization"])(
    "does not present the previous %s's cached facts while the new read fails",
    async (changed) => {
      const { result, rerender, responses } = open();
      await waitFor(() =>
        expect(embedding(result)?.providerAccessState).toBe("ready"),
      );
      responses[gw.managedModels] = new Error("New context unavailable");
      responses[gw.managedModelsCatalog] = new Error("New context unavailable");
      responses[gw.providers("default")] = new Error("New context unavailable");
      responses[gw.providers("second")] = new Error("New context unavailable");
      if (changed === "gateway") context.gatewayId = "second";
      else context.organizationId = "org-b";
      rerender();
      expect(result.current.rows).toBeUndefined();
      expect(result.current.providers.data).toBeUndefined();
      await waitFor(() => expect(result.current.status.error).toBeTruthy());
      expect(result.current.rows).toBeUndefined();
      expect(result.current.providers.data).toBeUndefined();
      responses[gw.managedModels] = {
        status: true,
        result: { available: true, enabled: false },
      };
      responses[gw.managedModelsCatalog] = {
        status: true,
        result: { models: [] },
      };
      responses[gw.providers(context.gatewayId)] = {
        status: true,
        result: { providers: [] },
      };
      await act(async () => {
        await result.current.status.refetch();
        await result.current.catalog.refetch();
        await result.current.providers.refetch();
      });
      await waitFor(() => expect(result.current.rows).toEqual([]));
      expect(result.current.providerAccessState).toBe("ready");
    },
  );
});
