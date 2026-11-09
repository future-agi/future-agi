import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";

import EarlyAccessPage from "../EarlyAccessPage";
import { rolloutQueryClient } from "./rollout-fixtures";
import { ShellTestProviders } from "./test-providers";

const DISCLOSURE =
  "Self-hosted Deployments is a preview. Run it only on a dedicated deploy-engine host you own, not shared with other workloads or tenants. Until a separate control daemon ships, the server and its deploy engine are root-equivalent on that host. Each deployment's outbound network is isolated in its own network namespace, which is on by default.";
const row = (product, label) => ({
  product,
  label,
  admitted: false,
  state: null,
  request_status: null,
});

function renderListing(products) {
  vi.spyOn(axios, "get").mockResolvedValue({
    data: { can_request: true, products },
  });
  render(
    <ShellTestProviders
      queryClient={rolloutQueryClient({ earlyAccess: false })}
    >
      <MemoryRouter initialEntries={["/dashboard/settings/early-access"]}>
        <Routes>
          <Route
            path="/dashboard/settings/early-access"
            element={<EarlyAccessPage />}
          />
          <Route path="/404" element={<div data-testid="page-404" />} />
        </Routes>
      </MemoryRouter>
    </ShellTestProviders>,
  );
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("Deployments preview on the enabling path", () => {
  it("shows the exact disclosure and Preview in the row before access is requested", async () => {
    const deployment = row("deployments", "Deployments");
    const post = vi.spyOn(axios, "post").mockResolvedValue({
      data: {
        can_request: true,
        products: [{ ...deployment, request_status: "requested" }],
      },
    });
    renderListing([deployment]);
    const scope = within(
      await screen.findByTestId("early-access-row-deployments"),
    );
    expect(scope.getByText(DISCLOSURE, { exact: true })).toBeVisible();
    expect(scope.getByText("Preview", { exact: true })).toBeVisible();
    expect(post).not.toHaveBeenCalled();
    await userEvent.click(
      scope.getByRole("button", { name: "Request access" }),
    );
    expect(await scope.findByText("Requested")).toBeVisible();
    expect(scope.getByText(DISCLOSURE, { exact: true })).toBeVisible();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(expect.any(String), {
      product: "deployments",
    });
  });

  it("does not introduce Deployments or its disclosure when hosted withholds the row", async () => {
    renderListing([row("console", "Console")]);
    await screen.findByTestId("early-access-row-console");
    expect(screen.queryByTestId("early-access-row-deployments")).toBeNull();
    expect(screen.queryByText(DISCLOSURE, { exact: true })).toBeNull();
    expect(screen.queryByText("Preview", { exact: true })).toBeNull();
  });

  it("preserves the hidden Early access page when no products are requestable", async () => {
    renderListing([]);
    await screen.findByTestId("page-404");
    expect(screen.queryByText(DISCLOSURE, { exact: true })).toBeNull();
  });
});
