import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";

import ProductGate from "../ProductGate";
import { OMEGA_PRODUCTS, capabilityIdFor } from "../rollout";
import { rolloutQueryClient } from "./rollout-fixtures";
import { ShellTestProviders } from "./test-providers";

function renderUnavailable(product) {
  const queryClient = rolloutQueryClient();
  queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
    data: {
      features: {
        [capabilityIdFor(product)]: {
          allowed: false,
          reason_code: "RESOLVER_UNAVAILABLE",
        },
      },
    },
  });

  render(
    <ShellTestProviders queryClient={queryClient}>
      <MemoryRouter>
        <ProductGate product={product}>
          <div>Product content</div>
        </ProductGate>
      </MemoryRouter>
    </ShellTestProviders>,
  );
}

const CONSOLE_DESCRIPTION = "Console access could not be confirmed. Try again.";
const SHARED_DESCRIPTION =
  "Future AGI couldn't confirm access just now. Try again in a few seconds.";

describe("ProductGate unavailable description", () => {
  it("uses the Console-only description without changing the title or retry button", () => {
    renderUnavailable("console");

    expect(screen.getByText(CONSOLE_DESCRIPTION)).toBeInTheDocument();
    expect(screen.queryByText(SHARED_DESCRIPTION)).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "Omega Console can't be opened right now",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(screen.queryByText("Product content")).not.toBeInTheDocument();
  });

  it.each(OMEGA_PRODUCTS.filter((product) => product !== "console"))(
    "keeps the shared description for %s",
    (product) => {
      renderUnavailable(product);

      expect(screen.getByText(SHARED_DESCRIPTION)).toBeInTheDocument();
      expect(screen.queryByText(CONSOLE_DESCRIPTION)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
      expect(screen.queryByText("Product content")).not.toBeInTheDocument();
    },
  );
});
