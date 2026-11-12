import { afterEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { loadOracle } from "./generate-v2-stock-oracle.mjs";
import {
  finishOracle,
  openApi,
  openDetails,
  showCatalog,
} from "./v2-oracle-harness";

// 713 disposition: the old snapshot stays byte-identical as an ARCHIVE, not
// a v2 baseline. Its model-key inventory anchors these exact-set assertions.
// All expectations derive from committed serializer data, never live renders.
const oracle = loadOracle();
const texts = (elements) => [...elements].map((el) => el.textContent);
const normalize = (text) => text.replace(/\s+/g, " ").trim();
const priceCells = (region) =>
  [...region.querySelectorAll("[data-row-id]")].map((row) =>
    texts(row.querySelectorAll("[role='gridcell']")),
  );

function assertCatalogue(row, expected) {
  const name = row.querySelector("[data-col='name']");
  expect(
    within(name).getByRole("link", { name: expected.name }),
  ).toHaveAttribute("href", expected.href);
  expect(
    within(name).getByText(expected.id, { exact: true }),
  ).toBeInTheDocument();
  expect(Boolean(within(name).queryByText("Not available on credits"))).toBe(
    !expected.sold,
  );
  expect(row.querySelector("[data-col='accessLabel']")).toHaveTextContent(
    expected.sold ? "Future AGI credits" : "Not callable",
  );
  expect(
    within(row.querySelector("[data-col='vendorName']")).getByText(
      expected.vendor,
      { exact: true },
    ),
  ).toBeInTheDocument();
  expect(row.querySelector("[data-col='released']").textContent).toBe(
    expected.released,
  );
  expect(row.querySelector("[data-col='context']").textContent).toBe(
    expected.context,
  );
  expect(row.querySelector("[data-col='maxOutput']").textContent).toBe(
    expected.maxOutput,
  );
  expect(row.querySelector("[data-col='capabilities']").textContent).toBe(
    expected.capabilities.join(", ") || "—",
  );
  const input = row.querySelector("[data-col='input']");
  const output = row.querySelector("[data-col='output']");
  if (expected.inclusive) {
    expect(input).toHaveAttribute("data-span", "2");
    expect(input).toHaveAttribute("data-auto-height", "true");
    expect(output).toBeNull();
    expect(input).not.toHaveTextContent("Free");
    if (expected.router) expect(input.textContent).toBe("Varies");
    else {
      // One price per dimension, what credits are charged: no provider
      // quote beside it, not even as a tooltip.
      const prices = input.querySelector("[aria-label='Prices by billing unit']");
      expect(texts(prices.children)).toEqual(expected.catalogPrices);
      expect(input.querySelectorAll("[aria-label^='Provider price']")).toHaveLength(0);
    }
  } else {
    expect([input.textContent, output.textContent]).toEqual(
      expected.catalogPrices,
    );
    expect(input).not.toHaveAttribute("data-span", "2");
    expect(
      row.querySelector("[aria-label='Prices by billing unit']"),
    ).toBeNull();
  }
  if (expected.retirement)
    expect(name).toHaveTextContent(`Retires ${expected.retirement}`);
  else if (expected.deprecated) expect(name).toHaveTextContent("Deprecated");
}

function assertCatalogueChrome(mode) {
  const expected = oracle.catalogueChrome[mode];
  const headers = screen.getAllByRole("columnheader");
  for (const column of ["input", "output"]) {
    const matches = headers.filter((el) => el.dataset.col === column);
    expect(matches).toHaveLength(1);
    expect(matches[0].textContent).toBe(expected[column].name);
    expect(matches[0]).toHaveAttribute("title", expected[column].tooltip);
  }
  const footers = screen.getAllByText(
    /^Prices are (?:per 1M tokens|what requests on Future AGI credits)/,
  );
  expect(footers).toHaveLength(1);
  const footer = footers[0];
  expect(normalize(footer.textContent)).toBe(expected.footer);
  // Exact full text above covers units, settlement and own-key billing; no
  // markup, percentage or "Future AGI price" is stated.
  expect(footer).not.toHaveTextContent(/markup|future agi price|provider \+|%/i);
  // Explicit negatives also prohibit the incompatible mode and additive claim.
  const otherMode = mode === "stock" ? "inclusive" : "stock";
  expect(footer).not.toHaveTextContent(
    oracle.catalogueChrome[otherMode].footer,
  );
  expect(footer).not.toHaveTextContent(/managed/i);
  expect(footer).not.toHaveTextContent(
    /Inclusive requests are billed (?:the )?sum of quoted dimensions/i,
  );
  expect(footer).not.toHaveTextContent(
    /own provider keys are billed by that provider, (?:with|plus) (?:a )?5%/i,
  );
  if (mode === "stock") expect(footer).not.toHaveTextContent(/inclusive/i);
  else {
    expect(headers.map((el) => el.textContent)).not.toContain("Input / 1M");
    expect(footer).not.toHaveTextContent("Prices are per 1M tokens:");
  }
}

function assertCapabilities(expected) {
  const region = screen.getByRole("region", { name: "Capabilities" });
  const parameterHeading = within(region).queryByText(
    "Supported request parameters",
    { exact: true },
  );
  const parameterGroup = parameterHeading?.parentElement;
  // MUI's stable Chip root contains the actual rendered label. Keep the two
  // collections separate; a spurious chip outside the parameter block is a
  // capability, not an ignored descendant. Array equality rejects duplicates.
  const chips = [...region.querySelectorAll(".MuiChip-root")];
  const parameters = chips.filter((el) => parameterGroup?.contains(el));
  const capabilities = chips.filter((el) => !parameterGroup?.contains(el));
  expect(texts(capabilities)).toEqual(expected.capabilities);
  expect(texts(parameters)).toEqual(expected.parameters);
  expect(new Set(texts(capabilities)).size).toBe(capabilities.length);
  expect(new Set(texts(parameters)).size).toBe(parameters.length);

  const empty = within(region).queryByText(
    "No tools, structured output, image input or reasoning controls listed.",
    { exact: true },
  );
  if (expected.capabilities.length) expect(empty).not.toBeInTheDocument();
  else {
    expect(capabilities).toHaveLength(0);
    expect(empty).toBeInTheDocument();
  }
  if (expected.parameters.length) expect(parameterHeading).toBeInTheDocument();
  else {
    expect(parameterHeading).not.toBeInTheDocument();
    expect(parameters).toHaveLength(0);
  }
}

function assertOverview(pricing, expected) {
  expect(
    screen.getByRole("heading", { level: 1, name: expected.name }),
  ).toBeInTheDocument();
  expect(
    screen.getAllByText(expected.id, { exact: true }).length,
  ).toBeGreaterThan(0);
  expect(
    screen.getByRole("button", { name: "Copy model ID" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Catalog" })).toHaveAttribute(
    "href",
    "/dashboard/gateway/models",
  );
  expect(
    screen.getByText(`${expected.vendor} · Chat`, { exact: true }),
  ).toBeInTheDocument();
  if (!expected.sold) expect(pricing).toHaveTextContent(expected.reason);
  expect(texts(within(pricing).queryAllByRole("columnheader"))).toEqual(
    expected.pricingHeaders,
  );
  expect(priceCells(pricing)).toEqual(expected.pricingRows);
  if (expected.inclusive) {
    expect(pricing).not.toHaveTextContent("Free");
    expect(pricing).not.toHaveTextContent("no per-token price");
    expect(pricing).toHaveTextContent("billed from the usage the provider reports");
    expect(pricing).not.toHaveTextContent(/markup|provider price|%/i);
    if (expected.router)
      expect(pricing).toHaveTextContent("This router has no fixed list price.");
    else expect(pricing).toHaveTextContent("not a sum of these rows");
  } else {
    expect(pricing).not.toHaveTextContent(/inclusive/i);
    if (!expected.pricingRows.length)
      expect(pricing).toHaveTextContent(expected.emptyPrice);
    for (const line of expected.perRequest)
      expect(within(pricing).getByText(line)).toBeInTheDocument();
  }
  if (expected.description) {
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === "P" &&
          normalize(el.textContent) === normalize(expected.description),
      ),
    ).toBeInTheDocument();
  }
  for (const [label, value] of [
    [
      "Context window",
      expected.context === "—" ? "—" : `${expected.context} tokens`,
    ],
    [
      "Max output",
      expected.maxOutput === "—" ? "—" : `${expected.maxOutput} tokens`,
    ],
    ["Input", expected.inputModalities],
    ["Output", expected.outputModalities],
    ["Released", expected.released],
    ["Endpoint", "POST /v1/chat/completions"],
  ]) {
    const fact = screen
      .getAllByText(label, { exact: true })
      .find((el) => el.parentElement?.textContent === label + value);
    expect(fact, `${expected.id}: ${label} = ${value}`).toBeDefined();
  }
  assertCapabilities(expected);
  const routes = screen.getByRole("region", { name: "Routes" });
  expect(priceCells(routes)).toHaveLength(expected.sold ? 1 : 0);
  if (expected.sold) expect(routes).toHaveTextContent(expected.id);
  else expect(routes).toHaveTextContent("To call it, add a provider");
  expect(screen.getByRole("region", { name: "Live health" })).toHaveTextContent(
    expected.sold
      ? "No requests to this model in the last 24 hours."
      : "No route reaches this model, so it has no traffic.",
  );
}

export function defineV2OracleSuite(mode, catalogue) {
  afterEach(finishOracle);
  // Yield to the worker event loop so pending task-update RPCs can complete.
  afterEach(() => new Promise((resolve) => setTimeout(resolve, 0)));

  describe(`v2 ${mode}: frozen 463-model catalogue AND drawer replacement`, () => {
    it("renders exactly the frozen model set, never missing/extra/duplicate models", () => {
      expect(catalogue.models).toHaveLength(463);
      const { container } = showCatalog(catalogue);
      const rendered = [...container.querySelectorAll("[data-model-id]")]
        .map((el) => el.dataset.modelId)
        .sort();
      expect(rendered).toEqual(oracle.ids);
      expect(new Set(rendered).size).toBe(463);
      expect(screen.getByText("463 models")).toBeInTheDocument();
      expect(screen.getByText("Bundled price list")).toBeInTheDocument();
    });

    it("pins both price headers, units and the complete billing explanation", () => {
      showCatalog(catalogue);
      assertCatalogueChrome(mode);
    });

    it.each(catalogue.models)(
      "catalogue + drawer + API exact contract: $id",
      (model) => {
        const expected = oracle.catalogues[mode][model.id];
        const { container } = showCatalog(catalogue, [model]);
        const rows = container.querySelectorAll("[data-model-id]");
        expect(rows).toHaveLength(1);
        expect(rows[0].dataset.modelId).toBe(model.id);
        assertCatalogue(rows[0], expected);
        assertCatalogueChrome(expected.inclusive ? "inclusive" : "stock");
        const pricing = openDetails(rows[0]);
        assertOverview(pricing, expected);
        openApi();
        expect(
          Boolean(screen.queryByRole("heading", { name: "Call this model" })),
        ).toBe(expected.sold);
        if (expected.sold) {
          expect(
            screen.getAllByRole("button", { name: "cURL" }).length,
          ).toBeGreaterThan(0);
          expect(
            screen.getAllByText(
              (_, el) =>
                el?.tagName === "CODE" && el.textContent.includes(model.id),
            ).length,
          ).toBeGreaterThan(0);
        } else {
          expect(
            screen.getByRole("heading", {
              name: "No request to copy for this model",
            }),
          ).toBeInTheDocument();
          expect(
            screen.queryByRole("button", { name: "cURL" }),
          ).not.toBeInTheDocument();
        }
      },
    );
  });
}
