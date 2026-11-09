// R7-ORGMODELS NG-33 and NG-27: the Auto routing tab says how auto prices a
// candidate and marks each of your providers' models with the price auto
// will use, or with why it may set the model aside (CA-F13: an own model
// the gateway could not price was offered unmarked and "How auto works" did
// not name the rule); the preference card names route-hint.
import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
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
import HowAutoChooses from "./auto/HowAutoChooses";
import { orgPriceOf } from "./autoRouting";
import { CATALOG_MODELS } from "./test-fixtures/catalog";

const { post, get, config } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  config: { current: {} },
}));

vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      config: (id) => `/gateway/${id}/config`,
      updateConfig: (id) => `/gateway/${id}/config/update`,
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: () => {} }));

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["gpt-4o", "my-llama-70b", "my-tuned-model"],
}));

const renderTab = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          mutationCache: new MutationCache({ onError: () => {} }),
          defaultOptions: { queries: { retry: false } },
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

// The org prices my-llama-70b in Cost Tracking ($0.05 / $0.20 per 1M) and
// gpt-4o's input only in Model Database ($1 per 1M); my-tuned-model has no
// price from the org.
const ORG = {
  routing: { strategy: "cost" },
  cost_tracking: {
    enabled: true,
    custom_pricing: {
      "my-llama-70b": { input_per_mtok: 0.05, output_per_mtok: 0.2 },
    },
  },
  model_database: {
    overrides: {
      "my-llama-70b": {
        pricing: { input_per_token: 0.000009, output_per_token: 0.000009 },
      },
      "gpt-4o": { pricing: { input_per_token: 0.000001 } },
    },
  },
};

beforeEach(() => {
  post.mockReset();
  get.mockReset();
  config.current = ORG;
  get.mockImplementation(async () => ({
    data: { status: true, result: config.current },
  }));
});

const ownOption = async (id) => {
  const input = await screen.findByLabelText("Candidate models");
  await waitFor(() => expect(input).toBeEnabled());
  if (!screen.queryByRole("listbox")) await userEvent.click(input);
  const listbox = await screen.findByRole("listbox");
  const group = within(listbox).getByText("Your providers").closest("li");
  return within(group).getByText(id).closest("li");
};

describe("AutoRoutingTab (R7 org pricing)", () => {
  it("marks each of your providers' models with the price auto uses, or why it may skip it", async () => {
    renderTab();
    expect((await ownOption("my-llama-70b")).textContent).toContain(
      "Your price (Cost Tracking): $0.05 in / $0.20 out per 1M",
    );
    expect((await ownOption("gpt-4o")).textContent).toContain(
      "Your price (Model Database): $1.00 in / model database out per 1M",
    );
    expect((await ownOption("my-tuned-model")).textContent).toContain(
      "No price from your org: auto skips it unless the gateway's model database prices this model",
    );
  });

  // How auto chooses is its own section on the Auto routing page, above the
  // settings this tab holds; the preference card stays in the tab.
  it("states the price rule and route-hint", async () => {
    render(<HowAutoChooses managedOffered={false} />);
    const how = screen.getByRole("region", { name: "How auto chooses" });
    expect(how).toHaveTextContent(
      "priced from your Cost Tracking price, else your Model Database price, else the gateway's model database",
    );
    expect(how).toHaveTextContent("set aside as having no price data");
    // This deployment offers no Future AGI models (managedStatus.available
    // false), so the card says nothing about their price.
    expect(how).not.toHaveTextContent("Future AGI");
    expect(how).toHaveTextContent(
      "a capability the request's require header asks for",
    );
    // The neutral ranking sentence stays.
    expect(how).toHaveTextContent(
      "Auto ranks the models your providers serve by how well their price tier fits the task and your preference.",
    );
    renderTab();
    expect(
      await screen.findByText(
        /How model "auto" trades price against capability/,
      ),
    ).toHaveTextContent("route-hint (cost, balanced or quality)");
  });
});

describe("orgPriceOf", () => {
  it("follows the gateway's precedence", () => {
    expect(orgPriceOf(ORG, "my-llama-70b")).toEqual({
      source: "Cost Tracking",
      input: 0.05,
      output: 0.2,
    });
    // Cost tracking off: its prices are not the org's; Model Database is.
    const off = {
      ...ORG,
      cost_tracking: { ...ORG.cost_tracking, enabled: false },
    };
    expect(orgPriceOf(off, "my-llama-70b").source).toBe("Model Database");
    expect(orgPriceOf(off, "my-llama-70b").input).toBeCloseTo(9);
    // A provider-prefixed id finds the bare model's entry.
    expect(orgPriceOf(ORG, "openai/gpt-4o")).toEqual({
      source: "Model Database",
      input: 1,
      output: null,
    });
    expect(orgPriceOf(ORG, "my-tuned-model")).toBeNull();
    expect(orgPriceOf(undefined, "gpt-4o")).toBeNull();
  });
});
