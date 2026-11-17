import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  AGENT_INFRASTRUCTURE_SUBHEADER,
  withAgentInfrastructure,
} from "../agent-infrastructure-nav";
import ProductSwitcher from "../ProductSwitcher";
import {
  OMEGA_PRODUCTS,
  PRODUCT_STATUS,
  isListed,
  productStatus,
} from "../rollout";
import { capabilitiesAnswer } from "./rollout-answer";
import { rolloutQueryClient } from "./rollout-fixtures";
import { ShellTestProviders } from "./test-providers";

// RC-01's admission-only nav and switcher helpers (READINESS 3.13). The nav
// adds inert hosted-withheld sidebar rows only with explicit venue + part
// metadata; these fixtures do not supply that metadata. Route admission and
// the switcher's product list are unchanged.

const SETTLED = { isLoading: false, isError: false };

describe("productStatus", () => {
  it("reads the server's answer and fails closed on everything else", () => {
    const answer = capabilitiesAnswer({
      admitted: ["console", "hub"],
      paused: ["hub"],
    });
    expect(productStatus(answer, "console", SETTLED)).toBe(
      PRODUCT_STATUS.ADMITTED,
    );
    expect(productStatus(answer, "hub", SETTLED)).toBe(PRODUCT_STATUS.PAUSED);
    expect(productStatus(answer, "sandboxes", SETTLED)).toBe(
      PRODUCT_STATUS.HIDDEN,
    );
    expect(productStatus(undefined, "console", SETTLED)).toBe(
      PRODUCT_STATUS.HIDDEN,
    );
    expect(productStatus({ features: {} }, "console", SETTLED)).toBe(
      PRODUCT_STATUS.HIDDEN,
    );
    expect(
      productStatus(answer, "console", { isLoading: true, isError: false }),
    ).toBe(PRODUCT_STATUS.LOADING);
    expect(
      productStatus(answer, "console", { isLoading: false, isError: true }),
    ).toBe(PRODUCT_STATUS.UNAVAILABLE);
    const resolver = {
      features: {
        omega_console: { allowed: false, reason_code: "RESOLVER_UNAVAILABLE" },
      },
    };
    expect(productStatus(resolver, "console", SETTLED)).toBe(
      PRODUCT_STATUS.UNAVAILABLE,
    );
    // Only admitted and paused are listed in the nav.
    expect(Object.values(PRODUCT_STATUS).filter(isListed)).toEqual([
      PRODUCT_STATUS.ADMITTED,
      PRODUCT_STATUS.PAUSED,
    ]);
  });
});

// P22 hosted switch (hosted_sandboxes, hosted_deployments): on hosted Future
// AGI the server withholds Sandboxes and Deployments from every org
// (omega_rollout/service.py _hosted_withheld, admission reason
// "hosted_withheld"). check_rollout sends every refused admission as
// PRODUCT_NOT_ADMITTED, so that is the wire shape; the raw reason is also
// read as hidden, in case a server ever sends it.
describe("productStatus, hosted switch (P22)", () => {
  const withheld = (reasonCode) => ({
    features: {
      omega_console: { allowed: true, reason_code: null },
      omega_sandboxes: { allowed: false, reason_code: reasonCode },
      omega_deployments: { allowed: false, reason_code: reasonCode },
    },
  });

  it.each(/** @type {const} */ (["sandboxes", "deployments"]))(
    "%s withheld on hosted is HIDDEN, in the server's wire shape and by the raw reason 'hosted_withheld'",
    (product) => {
      for (const reasonCode of ["PRODUCT_NOT_ADMITTED", "hosted_withheld"]) {
        const status = productStatus(withheld(reasonCode), product, SETTLED);
        expect(status).toBe(PRODUCT_STATUS.HIDDEN);
        expect(isListed(status)).toBe(false);
      }
      expect(
        productStatus(withheld("hosted_withheld"), "console", SETTLED),
      ).toBe(PRODUCT_STATUS.ADMITTED);
    },
  );

  it.each(/** @type {const} */ (["sandboxes", "deployments"]))(
    "%s admitted (the switch declared, or a self-hosted install) is ADMITTED and listed",
    (product) => {
      const answer = capabilitiesAnswer({ admitted: [product] });
      const status = productStatus(answer, product, SETTLED);
      expect(status).toBe(PRODUCT_STATUS.ADMITTED);
      expect(isListed(status)).toBe(true);
    },
  );

  // A denial without hosted venue/part metadata still invents no row.
  it("Sandboxes and Deployments denied without hosted metadata leave no nav or switcher entry", async () => {
    const answer = withheld("PRODUCT_NOT_ADMITTED");
    const listed = (product) =>
      isListed(productStatus(answer, product, SETTLED));
    const sections = withAgentInfrastructure(
      [{ subheader: "BUILD", items: [] }],
      true,
      listed,
    );
    const titles = sections.flatMap((s) => s.items.map((item) => item.title));
    expect(titles).not.toContain("Sandboxes");
    expect(titles).not.toContain("Deployments");
    // Only the products the server admits leave an entry: none of the
    // agent-infrastructure products here, so no group at all.
    expect(sections.map((s) => s.subheader)).toEqual(["BUILD"]);

    renderSwitcher(
      rolloutQueryClient({
        admitted: OMEGA_PRODUCTS.filter(
          (p) => p !== "sandboxes" && p !== "deployments",
        ),
      }),
    );
    await userEvent.click(screen.getByTestId("omega-product-switcher"));
    const menu = screen.getByRole("menu", { name: "Products" });
    expect(within(menu).getByText("Omega Console")).toBeInTheDocument();
    expect(within(menu).queryByText(/Sandboxes/)).toBeNull();
    expect(within(menu).queryByText(/Deployments/)).toBeNull();
  });

  it("the switch's self-hosted state: Sandboxes and Deployments admitted are listed in the nav", () => {
    const answer = capabilitiesAnswer({
      admitted: ["sandboxes", "deployments"],
    });
    const listed = (product) =>
      isListed(productStatus(answer, product, SETTLED));
    const [, group] = withAgentInfrastructure(
      [{ subheader: "BUILD", items: [] }],
      true,
      listed,
    );
    expect(group.items.map((item) => item.title)).toEqual([
      "Deployments",
      "Sandboxes",
    ]);
  });
});

describe("Agent infrastructure nav under rollout", () => {
  const HOST = [{ subheader: "BUILD", items: [] }];

  it("lists only admitted or paused products", () => {
    const listed = (product) => ["deployments", "hub"].includes(product);
    const [, group] = withAgentInfrastructure(HOST, true, listed);
    expect(group.subheader).toBe(AGENT_INFRASTRUCTURE_SUBHEADER);
    expect(group.items.map((item) => item.title)).toEqual([
      "Deployments",
      "Hub",
    ]);
    expect(group.items.every((item) => !("product" in item))).toBe(true);
  });

  it("adds no group at all when nothing is admitted (B)", () => {
    expect(withAgentInfrastructure(HOST, true, () => false)).toBe(HOST);
  });
});

function renderSwitcher(queryClient, path = "/dashboard/develop") {
  render(
    <ShellTestProviders queryClient={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <ProductSwitcher />
      </MemoryRouter>
    </ShellTestProviders>,
  );
}

describe("Product switcher under rollout", () => {
  it("A sees the Omega Console entry", async () => {
    renderSwitcher(rolloutQueryClient());
    await userEvent.click(screen.getByTestId("omega-product-switcher"));
    const menu = screen.getByRole("menu", { name: "Products" });
    expect(within(menu).getByText("Omega Console")).toBeInTheDocument();
  });

  it.each([
    ["B, nothing admitted", []],
    ["A2, console denied", OMEGA_PRODUCTS.filter((p) => p !== "console")],
  ])(
    // The AI Gateway is always listed (gateway UI v2, merged in round 7): the
    // switcher stays, and only the Console entry follows the rollout.
    "%s: no Console entry, only Future AGI and the AI Gateway",
    async (_label, admitted) => {
      renderSwitcher(rolloutQueryClient({ admitted }));
      await userEvent.click(screen.getByTestId("omega-product-switcher"));
      expect(screen.getByText("AI Gateway")).toBeInTheDocument();
      expect(screen.queryByText("Omega Console")).not.toBeInTheDocument();
    },
  );

  it("collapsed, B gets the switcher with no Console entry", async () => {
    render(
      <ShellTestProviders queryClient={rolloutQueryClient({ admitted: [] })}>
        <MemoryRouter initialEntries={["/dashboard/develop"]}>
          <ProductSwitcher collapsed />
        </MemoryRouter>
      </ShellTestProviders>,
    );
    await userEvent.click(screen.getByTestId("omega-product-switcher"));
    expect(screen.getByText("AI Gateway")).toBeInTheDocument();
    expect(screen.queryByText("Omega Console")).not.toBeInTheDocument();
  });

  it("a paused Console keeps its entry (it does not vanish for its users)", async () => {
    renderSwitcher(rolloutQueryClient({ paused: ["console"] }));
    await userEvent.click(screen.getByTestId("omega-product-switcher"));
    expect(screen.getByText("Omega Console")).toBeInTheDocument();
  });
});
