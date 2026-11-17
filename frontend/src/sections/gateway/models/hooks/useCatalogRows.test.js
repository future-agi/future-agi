import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { endpoints } from "src/utils/axios";
import {
  answerFrom,
  contractFailures,
  demoResponses,
} from "../test-fixtures/harness";
import { useCatalogRows } from "./useCatalogRows";
import {
  MANAGED_CATALOG_QUERY_KEY,
  MANAGED_MODELS_QUERY_KEY,
  useSetManagedModelsEnabled,
} from "./useManagedModels";

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
const clients = [];
const makeClient = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  return client;
};
const mount = (client, useHook = useCatalogRows) =>
  renderHook(() => useHook(), {
    wrapper: ({ children }) =>
      React.createElement(QueryClientProvider, { client }, children),
  });
const open = () => {
  const responses = demoResponses(endpoints);
  api.get.mockImplementation(answerFrom(responses));
  const client = makeClient();
  return { ...mount(client), client, responses };
};
const ready = async (hook) => {
  await waitFor(() => {
    expect(hook.result.current.rows?.length).toBeGreaterThan(0);
    expect(hook.result.current.providerAccessState).toBe("ready");
  });
};
const failReads = (responses) => {
  // The shared axios interceptor rejects with statusCode, not response.status.
  const error = { statusCode: 503, message: "Gateway unavailable" };
  for (const url of [
    gw.managedModels,
    gw.managedModelsCatalog,
    gw.providers(context.gatewayId),
  ]) {
    responses[url] = () => Promise.reject(error);
  }
  return error;
};
const expectStale = async (hook, error) => {
  await waitFor(() => {
    expect(hook.result.current.status.error).toBe(error);
    expect(hook.result.current.catalog.error).toBe(error);
    expect(hook.result.current.providers.error).toBe(error);
  });
};

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  context.gatewayId = "default";
  context.organizationId = "org-a";
});

afterEach(async () => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  expect(await contractFailures(api)).toEqual([]);
});

describe("catalog cache after managed enablement mutations", () => {
  it.each(["inactive", "confirmed", "revalidating"])(
    "does not restore enabled models after disabling with an %s catalog and a failed refresh",
    async (state) => {
      const first = open();
      await ready(first);
      expect(first.result.current.status.data.enabled).toBe(true);
      expect(first.result.current.rows.some((row) => row.managedOn)).toBe(true);
      if (state !== "confirmed") first.unmount();
      const error = failReads(first.responses);
      const active = state === "revalidating" ? mount(first.client) : first;
      if (state === "revalidating") await expectStale(active, error);
      const mutation = mount(first.client, useSetManagedModelsEnabled);
      api.post.mockResolvedValue({
        data: { status: true, result: { gateway_synced: true } },
      });

      await act(async () => {
        await mutation.result.current.mutateAsync({
          gatewayId: context.gatewayId,
          enabled: false,
        });
      });
      expect(api.post).toHaveBeenCalledTimes(1);
      expect(api.post).toHaveBeenCalledWith(
        gw.updateConfig("default"),
        { managed_models: { enabled: false } },
        expect.objectContaining({ timeout: 30000 }),
      );
      expect(
        first.client.getQueryData([MANAGED_MODELS_QUERY_KEY]).enabled,
      ).toBe(false);
      if (state !== "inactive") {
        await waitFor(() =>
          expect(active.result.current.status.data?.enabled).not.toBe(true),
        );
        active.unmount();
      }

      const next = mount(first.client);
      expect(next.result.current.status.data?.enabled).not.toBe(true);
      expect(next.result.current.rows?.some((row) => row.managedOn)).not.toBe(true);
      await waitFor(() => expect(next.result.current.status.error).toBe(error));
      expect(next.result.current.status.data?.enabled).not.toBe(true);
      expect(next.result.current.rows?.some((row) => row.managedOn)).not.toBe(true);

      // A later valid read can establish fresh facts, without losing the models.
      Object.assign(first.responses, demoResponses(endpoints), {
        [gw.managedModels]: {
          status: true,
          result: { available: true, enabled: false },
        },
      });
      await act(async () => {
        await next.result.current.status.refetch();
        await next.result.current.catalog.refetch();
        await next.result.current.providers.refetch();
      });
      await ready(next);
      expect(next.result.current.status.data.enabled).toBe(false);
      expect(next.result.current.rows.every((row) => !row.managedOn)).toBe(true);
      expect(next.result.current.status.error).toBeNull();
    },
  );

  it("keeps validated facts when the disable request fails", async () => {
    const first = open();
    await ready(first);
    first.unmount();
    const error = failReads(first.responses);
    const mutation = mount(first.client, useSetManagedModelsEnabled);
    const rejected = { statusCode: 403, message: "Change refused" };
    api.post.mockRejectedValue(rejected);

    await act(async () => {
      await expect(
        mutation.result.current.mutateAsync({
          gatewayId: context.gatewayId,
          enabled: false,
        }),
      ).rejects.toBe(rejected);
    });
    expect(api.post).toHaveBeenCalledTimes(1);
    const next = mount(first.client);
    await expectStale(next, error);
    expect(next.result.current.status.data.enabled).toBe(true);
    expect(next.result.current.rows.some((row) => row.managedOn)).toBe(true);
  });
});

describe("catalog cache provenance across component lifetimes", () => {
  afterEach(() => expect(api.post).not.toHaveBeenCalled());

  it("retains same-scope facts with stale errors on remount and recovers on Retry", async () => {
    const first = open();
    await ready(first);
    const previous = first.result.current;
    const ids = previous.rows.map((row) => row.id);
    first.unmount();
    const error = failReads(first.responses);

    const next = mount(first.client);
    expect(next.result.current.rows?.map((row) => row.id)).toEqual(ids);
    await expectStale(next, error);
    expect(next.result.current.status.data).toEqual(previous.status.data);
    expect(next.result.current.catalog.data).toEqual(previous.catalog.data);
    expect(next.result.current.providers.data).toEqual(previous.providers.data);
    expect(next.result.current.rows.map((row) => row.id)).toEqual(ids);
    expect(next.result.current.providerAccessState).toBe("error");
    expect(
      next.result.current.rows.find(
        (row) => row.id === "openai/text-embedding-3-small",
      ),
    ).toMatchObject({
      accessLabel: "Access not verified",
      ownRoutes: previous.rows.find(
        (row) => row.id === "openai/text-embedding-3-small",
      ).ownRoutes,
    });
    expect(next.result.current.isLoading).toBe(false);

    first.responses[gw.managedModels] = {
      status: true,
      result: { available: true, enabled: false },
    };
    first.responses[gw.managedModelsCatalog] = {
      status: true,
      result: { models: [] },
    };
    first.responses[gw.providers("default")] = {
      status: true,
      result: { providers: [] },
    };
    await act(async () => {
      await next.result.current.status.refetch();
      await next.result.current.catalog.refetch();
      await next.result.current.providers.refetch();
    });
    await waitFor(() => expect(next.result.current.rows).toEqual([]));
    expect(next.result.current.status.data.enabled).toBe(false);
    expect(next.result.current.status.error).toBeNull();
    expect(next.result.current.catalog.error).toBeNull();
    expect(next.result.current.providers.error).toBeNull();
    expect(next.result.current.providerAccessState).toBe("ready");
  });

  it.each(["gateway", "organization"])(
    "never restores another %s's facts on a failed remount",
    async (changed) => {
      const first = open();
      await ready(first);
      first.unmount();
      if (changed === "gateway") context.gatewayId = "second";
      else context.organizationId = "org-b";
      const error = failReads(first.responses);

      const next = mount(first.client);
      expect(next.result.current.rows).toBeUndefined();
      expect(next.result.current.providers.data).toBeUndefined();
      await waitFor(() => {
        expect(next.result.current.status.error).toBe(error);
        expect(next.result.current.providers.error).toBe(error);
      });
      expect(next.result.current.status.data).toBeUndefined();
      expect(next.result.current.catalog.data).toBeUndefined();
      expect(next.result.current.rows).toBeUndefined();
      expect(next.result.current.providers.data).toBeUndefined();
    },
  );

  it("restores only the returning scope's facts after another organization replaced the legacy cache", async () => {
    const first = open();
    await ready(first);
    const previous = first.result.current;
    first.unmount();
    context.organizationId = "org-b";
    first.responses[gw.managedModels] = {
      status: true,
      result: { available: true, enabled: false },
    };
    first.responses[gw.managedModelsCatalog] = {
      status: true,
      result: { models: [] },
    };
    first.responses[gw.providers("default")] = {
      status: true,
      result: { providers: [] },
    };
    const other = mount(first.client);
    await waitFor(() => {
      expect(other.result.current.rows).toEqual([]);
      expect(other.result.current.providerAccessState).toBe("ready");
    });
    other.unmount();
    context.organizationId = "org-a";
    const error = failReads(first.responses);

    const returning = mount(first.client);
    await expectStale(returning, error);
    expect(returning.result.current.status.data).toEqual(previous.status.data);
    expect(returning.result.current.catalog.data).toEqual(previous.catalog.data);
    expect(returning.result.current.providers.data).toEqual(previous.providers.data);
    expect(returning.result.current.rows.map((row) => row.id)).toEqual(
      previous.rows.map((row) => row.id),
    );
  });

  it("keeps the last validated catalog across remount after a malformed refresh", async () => {
    const first = open();
    await ready(first);
    const catalog = first.result.current.catalog.data;
    first.responses[gw.managedModelsCatalog] = { status: true, result: {} };
    await act(async () => {
      await first.result.current.catalog.refetch();
    });
    expect(first.result.current.catalog.error).toBeTruthy();
    first.unmount();
    const error = failReads(first.responses);

    const next = mount(first.client);
    await expectStale(next, error);
    expect(next.result.current.catalog.data).toEqual(catalog);
    expect(next.result.current.rows?.length).toBeGreaterThan(0);
  });

  it.each(["new client", "cleared cache"])(
    "does not restore facts after a %s without new validation",
    async (reset) => {
      const first = open();
      await ready(first);
      first.unmount();
      const client = reset === "new client" ? makeClient() : first.client;
      if (reset === "cleared cache") client.clear();
      const error = failReads(first.responses);

      const next = mount(client);
      await waitFor(() => expect(next.result.current.status.error).toBe(error));
      expect(next.result.current.status.data).toBeUndefined();
      expect(next.result.current.catalog.data).toBeUndefined();
      expect(next.result.current.providers.data).toBeUndefined();
      expect(next.result.current.rows).toBeUndefined();
    },
  );

  it("does not treat unvalidated legacy query data as same-scope provenance", async () => {
    const client = makeClient();
    const responses = demoResponses(endpoints);
    api.get.mockImplementation(answerFrom(responses));
    client.setQueryData(
      [MANAGED_MODELS_QUERY_KEY],
      responses[gw.managedModels].result,
    );
    client.setQueryData(
      [MANAGED_CATALOG_QUERY_KEY],
      responses[gw.managedModelsCatalog].result,
    );
    client.setQueryData(
      ["agentcc-provider-health", "default"],
      responses[gw.providers("default")].result,
    );
    const error = failReads(responses);

    const next = mount(client);
    await waitFor(() => expect(next.result.current.status.error).toBe(error));
    expect(next.result.current.rows).toBeUndefined();
    expect(next.result.current.status.data).toBeUndefined();
    expect(next.result.current.providers.data).toBeUndefined();
  });
});
