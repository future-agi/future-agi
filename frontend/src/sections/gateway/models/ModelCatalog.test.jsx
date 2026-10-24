import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  within,
} from "src/utils/test-utils";
import ModelCatalog from "./ModelCatalog";
import { CATALOG } from "./test-fixtures/catalog";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const ENABLED = {
  available: true,
  enabled: true,
  balance_usd: 10,
  markup_percent: 5,
};

const renderCatalog = (props = {}) =>
  render(
    <ModelCatalog
      catalog={CATALOG}
      managedStatus={ENABLED}
      baseUrl="https://gw.example.com/v1"
      onRetry={vi.fn()}
      {...props}
    />,
  );

// Data rows of the catalog table, in display order, by model ID.
const rowIds = () =>
  within(screen.getByRole("table", { name: "Model catalog" }))
    .getAllByRole("row")
    .slice(1)
    .map((row) => row.querySelector("[class*='caption']")?.textContent);

describe("ModelCatalog", () => {
  it("lists the catalog with billed prices", () => {
    renderCatalog();
    expect(rowIds()).toHaveLength(5);
    expect(screen.getByText("5 models")).toBeInTheDocument();

    const claude = screen.getByRole("row", {
      name: "Claude Sonnet 5.5 details",
    });
    expect(within(claude).getByText("$3.15")).toBeInTheDocument();
    expect(within(claude).getByText("$15.75")).toBeInTheDocument();
    expect(within(claude).getByText("1M")).toBeInTheDocument();
    expect(
      within(
        screen.getByRole("row", { name: "Sonar Deep Research details" }),
      ).getAllByText("Varies"),
    ).toHaveLength(2);
    expect(screen.getByText(/Prices refreshed/)).toBeInTheDocument();
  });

  it("says when prices come from the bundled list rather than a live fetch", () => {
    renderCatalog({
      catalog: {
        ...CATALOG,
        providers: [{ ...CATALOG.providers[0], source: "bundled" }],
      },
    });
    expect(
      screen.getByText(
        /Using the bundled price list; live prices are unavailable/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Prices refreshed/)).not.toBeInTheDocument();
  });

  it("narrows by search", async () => {
    renderCatalog();
    await userEvent.type(screen.getByLabelText("Search models"), "claude");
    expect(rowIds()).toEqual(["anthropic/claude-sonnet-5.5"]);
    expect(screen.getByText("1 of 5 models")).toBeInTheDocument();
  });

  it("filters by capability chips", async () => {
    renderCatalog();
    const chips = screen.getByRole("group", { name: "Filter by capability" });
    await userEvent.click(within(chips).getByText("Image input"));
    expect(rowIds()).toEqual([
      "anthropic/claude-sonnet-5.5",
      "openai/gpt-5-mini",
    ]);
    await userEvent.click(within(chips).getByText("Reasoning"));
    expect(rowIds()).toEqual(["anthropic/claude-sonnet-5.5"]);
  });

  it("sorts by price", async () => {
    renderCatalog();
    fireEvent.mouseDown(screen.getByLabelText("Sort"));
    await userEvent.click(
      screen.getByRole("option", { name: "Price: low to high" }),
    );
    expect(rowIds()).toEqual([
      "meta-llama/llama-4-scout:free",
      "openai/gpt-5-mini",
      "mystery-model",
      "anthropic/claude-sonnet-5.5",
      "perplexity/sonar-deep-research",
    ]);
  });

  it("offers to clear filters that match nothing", async () => {
    renderCatalog();
    await userEvent.type(screen.getByLabelText("Search models"), "nothing");
    expect(
      screen.getByText("No models match these filters"),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /Clear Filters/ }),
    );
    expect(rowIds()).toHaveLength(5);
  });

  it("opens a model's details with a ready-to-run request", async () => {
    renderCatalog();
    await userEvent.click(
      screen.getByRole("row", { name: "Claude Sonnet 5.5 details" }),
    );

    const pricing = await screen.findByRole("table", {
      name: "Pricing per 1M tokens",
    });
    const input = within(pricing).getByText("Input").closest("tr");
    expect(input).toHaveTextContent("$3");
    expect(input).toHaveTextContent("$3.15");
    expect(within(pricing).getByText("Cached input")).toBeInTheDocument();

    const code = screen.getByText(/curl https:\/\/gw\.example\.com\/v1/);
    expect(code).toHaveTextContent('"model": "anthropic/claude-sonnet-5.5"');

    await userEvent.click(screen.getByRole("button", { name: "Python" }));
    expect(
      screen.getByText(/base_url="https:\/\/gw\.example\.com\/v1"/),
    ).toBeInTheDocument();
  });

  it("nudges toward enabling managed models from a model's details", async () => {
    renderCatalog({ managedStatus: { ...ENABLED, enabled: false } });
    await userEvent.click(
      screen.getByRole("row", { name: "GPT-5 Mini details" }),
    );
    expect(
      await screen.findByText(/Turn on managed models above/),
    ).toBeInTheDocument();
  });

  it("explains a deployment without a managed catalog", () => {
    renderCatalog({
      catalog: undefined,
      managedStatus: { available: false, enabled: false },
    });
    expect(
      screen.getByText("No managed catalog on this deployment"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Go to Providers" }),
    ).toBeInTheDocument();
  });

  it("shows a retryable error", async () => {
    const onRetry = vi.fn();
    renderCatalog({
      catalog: undefined,
      error: new Error("Gateway error"),
      onRetry,
    });
    expect(
      screen.getByText(/Couldn't load the model catalog/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Try Again/ }));
    expect(onRetry).toHaveBeenCalled();
  });
});
