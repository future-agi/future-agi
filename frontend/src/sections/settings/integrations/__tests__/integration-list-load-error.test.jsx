import React from "react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "src/utils/test-utils";

import IntegrationsList from "../IntegrationsList";

// A failed read of the Integrations list is shown on the page, once: the page
// asks for its list with meta.errorHandled, so the app's query error handler
// adds no toast (src/utils/queryErrorHandler.js).

const h = vi.hoisted(() => ({
  listOptions: [],
  isLoading: false,
  isError: true,
}));

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: (options) => {
    h.listOptions.push(options);
    return { data: undefined, isLoading: h.isLoading, isError: h.isError };
  },
  useStartSlackInstall: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("../AddIntegrationWizard", () => ({ default: () => null }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
}));

beforeEach(() => {
  h.listOptions = [];
  h.isLoading = false;
  h.isError = true;
  window.history.pushState({}, "", "/");
});

const expectListError = () => {
  const alerts = screen.getAllByRole("alert");
  expect(alerts).toHaveLength(1);
  const error = within(alerts[0]);
  expect(
    error.getByText("Failed to load integrations", { exact: true }),
  ).toBeVisible();
  expect(
    error.getByText("Check your connection and retry.", { exact: true }),
  ).toBeVisible();
  expect(h.listOptions.at(-1)).toEqual({ meta: { errorHandled: true } });
};

describe("The Integrations page", () => {
  it("shows a failed list once, on the page", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <IntegrationsList />
      </QueryClientProvider>,
    );
    expectListError();
  });

  it("announces a failed list after loading", () => {
    h.isLoading = true;
    h.isError = false;
    const page = (
      <QueryClientProvider client={new QueryClient()}>
        <IntegrationsList />
      </QueryClientProvider>
    );
    const { rerender } = render(page);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    h.isLoading = false;
    h.isError = true;
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <IntegrationsList />
      </QueryClientProvider>,
    );
    expectListError();
  });
});
