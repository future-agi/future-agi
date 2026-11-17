// A saved Auto candidate whose model a provider holds back (its credential
// must be replaced) is listed as not offered; once the credential is
// replaced and the providers read offers the model again, the candidate is
// back among your providers' models.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import AutoRoutingTab from "./AutoRoutingTab";
import { CATALOG_MODELS } from "./test-fixtures/catalog";

const { post, get, config, providerRead } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  config: { current: {} },
  providerRead: { current: null },
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-one" }),
}));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      config: (id) => `/gateway/${id}/config`,
      updateConfig: (id) => `/gateway/${id}/config/update`,
      providers: (id) => `/gateway/${id}/providers`,
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: () => {} }));

const renderTab = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          mutationCache: new MutationCache({ onError: () => {} }),
          defaultOptions: {
            mutations: { retry: false },
            queries: { retry: false },
          },
        })
      }
    >
      <AutoRoutingTab
        gatewayId="gw-1"
        canWrite
        catalogModels={CATALOG_MODELS}
        managedStatus={{ available: false, enabled: false }}
        baseUrl="https://gw.example.com/v1"
      />
    </QueryClientProvider>,
  );

// The candidate list's group that holds this option.
const groupOf = async (id) => {
  const input = await screen.findByLabelText("Candidate models");
  await waitFor(() => expect(input).toBeEnabled());
  if (!screen.queryByRole("listbox")) await userEvent.click(input);
  const listbox = await screen.findByRole("listbox");
  const option = within(listbox).getByRole("option", {
    name: new RegExp(`^${id}`),
  });
  const group = option.closest("ul").closest("li");
  return group.firstChild.textContent;
};

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  config.current = { routing: { auto: { candidates: ["gpt-4o"] } } };
  get.mockImplementation(async (url) => {
    if (url.endsWith("/providers")) {
      return { data: { status: true, result: providerRead.current } };
    }
    return { data: { status: true, result: config.current } };
  });
});

describe("AutoRoutingTab: a held-back model after its credential is replaced", () => {
  it("lists a candidate the provider holds back as not offered", async () => {
    providerRead.current = {
      providers: [{ id: "openai", models: [], dropped_model_count: 2 }],
    };
    renderTab();
    expect(await groupOf("gpt-4o")).toBe("Not offered by any provider");
  });

  it("moves the candidate back into your providers once the model is offered again", async () => {
    providerRead.current = {
      providers: [{ id: "openai", models: ["gpt-4o", "my-own-model"] }],
    };
    renderTab();
    expect(await groupOf("gpt-4o")).toBe("Your providers");
    expect(post).not.toHaveBeenCalled();
  });
});
