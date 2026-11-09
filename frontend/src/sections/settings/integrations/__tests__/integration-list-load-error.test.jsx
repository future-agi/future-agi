import React from "react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";

import IntegrationsList from "../IntegrationsList";

// A failed read of the Integrations list is shown on the page, once: the page
// asks for its list with meta.errorHandled, so the app's query error handler
// adds no toast (src/utils/queryErrorHandler.js).

const h = vi.hoisted(() => ({ listOptions: [] }));

vi.mock("src/api/integrations", () => ({
  integrationKeys: { connections: () => ["integrations", "connections"] },
  useIntegrationConnections: (options) => {
    h.listOptions.push(options);
    return { data: undefined, isLoading: false, isError: true };
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

describe("The Integrations page", () => {
  it("shows a failed list once, on the page", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <IntegrationsList />
      </QueryClientProvider>,
    );
    expect(
      screen.getByText("Failed to load integrations. Please try again later."),
    ).toBeInTheDocument();
    expect(h.listOptions.at(-1)).toEqual({ meta: { errorHandled: true } });
  });
});
