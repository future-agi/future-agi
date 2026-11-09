import { afterEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { availableOnCredits } from "./catalog";
import { buildCatalogRows, filterRows } from "./catalogRows";
import inclusive from "./test-fixtures/inclusive-catalog.json";
import stock from "./test-fixtures/stock-catalog.json";
import request from "./test-fixtures/request-catalog.json";
import { inclusiveDimensions } from "./test-fixtures/generate-v2-stock-oracle.mjs";
import {
  finishOracle,
  openApi,
  openDetails,
  showCatalog,
} from "./test-fixtures/v2-oracle-harness";

const fixed = inclusive.models.filter(
  (m) => m.inclusive_price && !m.dynamic_price,
);
const routers = inclusive.models.filter(
  (m) => m.inclusive_price && m.dynamic_price,
);
const show = (model, catalogue = inclusive) => {
  const { container } = showCatalog(catalogue, [model]);
  const rows = container.querySelectorAll("[data-model-id]");
  expect(rows).toHaveLength(1);
  return rows[0];
};
const renderedPriceRows = (region) =>
  [...region.querySelectorAll("[data-row-id]")].map((row) =>
    [...row.querySelectorAll("[role='gridcell']")].map(
      (cell) => cell.textContent,
    ),
  );
afterEach(finishOracle);

describe("serialized inclusive catalogue pricing on v2", () => {
  it("covers the full qualified catalogue, including both paid generations", () => {
    expect(fixed.length + routers.length).toBe(34);
    expect(routers.length).toBeGreaterThan(0);
    expect(
      fixed
        .filter((m) => m.inclusive_price.generation)
        .map((m) => m.inclusive_price.generation),
    ).toEqual(["0.04", "0.08"]);
  });

  it.each(fixed)("renders every exact price and unit for $id", (model) => {
    const row = show(model);
    expect(row).not.toHaveTextContent("Free");
    expect(row).not.toHaveTextContent("Unavailable");
    const dimensions = inclusiveDimensions(model, inclusive.markup_percent);
    for (const r of dimensions) {
      expect(
        within(row).getByText(`${r.label}: ${r.charged} / ${r.unit}`),
      ).toHaveAttribute(
        "aria-label",
        `Provider price: ${r.provider} / ${r.unit}`,
      );
    }
    const pricing = openDetails(row);
    expect(pricing).not.toHaveTextContent("Free");
    expect(renderedPriceRows(pricing)).toEqual(
      dimensions.map((r) => [
        `${r.label} (per ${r.unit})`,
        r.provider,
        r.charged,
      ]),
    );
    expect(pricing).toHaveTextContent("not a sum of these rows");
  });

  it.each(fixed)(
    "drawer replacement independently renders inclusive dimensions: $id",
    (model) => {
      const pricing = openDetails(show(model));
      const dimensions = inclusiveDimensions(model, inclusive.markup_percent);
      expect(renderedPriceRows(pricing)).toEqual(
        dimensions.map((r) => [
          `${r.label} (per ${r.unit})`,
          r.provider,
          r.charged,
        ]),
      );
      expect(pricing).not.toHaveTextContent("Free");
      expect(pricing).toHaveTextContent("not a sum of these rows");
    },
  );

  it.each(routers)(
    "drawer replacement independently disclaims fixed router rates: $id",
    (model) => {
      const pricing = openDetails(show(model));
      expect(pricing).toHaveTextContent("This router has no fixed list price.");
      expect(pricing).toHaveTextContent("inclusive total + 5%");
      expect(renderedPriceRows(pricing)).toEqual([]);
      openApi();
      expect(
        screen.getByRole("heading", { name: "Call this model" }),
      ).toBeInTheDocument();
    },
  );

  it("does not round tiny image or cache-write prices to a threshold or cents", () => {
    const row = show(
      inclusive.models.find((m) => m.id === "google/gemini-4-argon"),
    );
    expect(
      within(row).getByText("Image: $0.0000021 / image"),
    ).toBeInTheDocument();
    expect(
      within(row).getByText("Cache write: $0.196875 / 1M tokens"),
    ).toBeInTheDocument();
    const pricing = openDetails(row);
    expect(within(pricing).getByText("$0.000002")).toBeInTheDocument();
    expect(within(pricing).getByText("$0.1875")).toBeInTheDocument();
  });

  it("preserves all serialized decimal places rather than rounding a quote", () => {
    const row = show(
      inclusive.models.find((m) => m.id === "google/gemini-3.6-flash"),
    );
    expect(
      within(row).getByText("Cache write: $0.043750000000000035 / 1M tokens"),
    ).toHaveAttribute(
      "aria-label",
      "Provider price: $0.0416666666666667 / 1M tokens",
    );
  });

  it.each(routers)(
    "offers qualified router $id without inventing a fixed rate",
    (model) => {
      expect(availableOnCredits(model)).toBe(true);
      const row = show(model);
      expect(row).not.toHaveTextContent("Not available on credits");
      expect(row).not.toHaveTextContent("Free");
      expect(within(row).getByText("Varies")).toBeInTheDocument();
      const pricing = openDetails(row);
      expect(pricing).toHaveTextContent("This router has no fixed list price.");
      expect(pricing).toHaveTextContent("inclusive total + 5%");
      expect(renderedPriceRows(pricing)).toEqual([]);
      openApi();
      expect(
        screen.getByRole("heading", { name: "Call this model" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/Not available on Future AGI credits/),
      ).not.toBeInTheDocument();
    },
  );

  it("keeps every unqualified dynamic model unavailable", () => {
    const unqualified = [
      ...stock.models,
      ...inclusive.models,
      ...request.models,
    ].filter((m) => m.dynamic_price && !m.inclusive_price);
    expect(unqualified.length).toBeGreaterThan(0);
    unqualified.forEach((m) => expect(availableOnCredits(m)).toBe(false));
    const row = show(unqualified[0]);
    expect(row).toHaveTextContent("Not available on credits");
    openDetails(row);
    openApi();
    expect(
      screen.queryByRole("heading", { name: "Call this model" }),
    ).not.toBeInTheDocument();
  });

  it("fails closed if a router's qualification is missing, mismatched or revoked", () => {
    const model = routers[0];
    for (const patch of [
      { inclusive_price: null },
      { inclusive_price: {} },
      {
        inclusive_price: { ...model.inclusive_price, model: "different-model" },
      },
      { inclusive_price: { ...model.inclusive_price, contract: "unknown" } },
      { inclusive_price: { ...model.inclusive_price, router_snapshot: "" } },
      {
        inclusive_price: {
          ...model.inclusive_price,
          router_snapshot: "invalid",
        },
      },
      { inclusive_price: fixed[0].inclusive_price },
      { state: "blocked" },
      { state: "unknown" },
      { state: undefined },
      { stale_price: true },
      { unpriced: true },
    ]) {
      const revoked = { ...model, ...patch };
      expect(availableOnCredits(revoked)).toBe(false);
      const row = show(revoked);
      expect(row).toHaveTextContent("Not available on credits");
      expect(row.querySelector("[data-col='input']").textContent).toBe("—");
      const pricing = openDetails(row);
      expect(pricing).not.toHaveTextContent(/inclusive total/);
      openApi();
      expect(
        screen.queryByRole("heading", { name: "Call this model" }),
      ).not.toBeInTheDocument();
    }
  });

  it("retains the real serialized per-request rate and unit", () => {
    const model = request.models.find((m) => m.id === "acme/request");
    expect(model.prices.request).toBe(0.04);
    const row = show(model, request);
    // SC2 v2 has ONE request-price cell, never duplicates the fee as output.
    expect(within(row).getAllByText("$0.042 / request")).toHaveLength(1);
    expect(row.querySelector("[data-col='output']").textContent).toBe("—");
    expect(row).not.toHaveTextContent("Free");
    const pricing = openDetails(row);
    expect(pricing).toHaveTextContent(
      "Request fee (per call): $0.04 from the provider, $0.042 on Future AGI credits",
    );
  });

  it("does not put inclusive generations or routers into Free or scalar price facets", () => {
    const rows = buildCatalogRows(inclusive.models, {
      managedStatus: { available: true, enabled: true, markup_percent: 5 },
    });
    const qualified = rows.filter((r) => r.model.inclusive_price);
    expect(qualified).toHaveLength(34);
    for (const price of ["free", "0.5", "2", "10"])
      expect(filterRows(qualified, { price })).toEqual([]);
  });
});
