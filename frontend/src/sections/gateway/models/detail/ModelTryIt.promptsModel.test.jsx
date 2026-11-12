import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import ModelTryIt from "./ModelTryIt";
import { ACCESS } from "../catalogRows";
import {
  laterPageCatalogs,
  modelListResponse,
} from "../../playground/__tests__/promptsModelFixtures";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get },
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));

let client;
beforeEach(() => get.mockReset());
afterEach(() => {
  cleanup();
  client?.clear();
});

function renderTryIt(id) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={createTheme({ palette: palette("light"), typography })}>
        <MemoryRouter>
          <ModelTryIt
            gatewayId="gw-1"
            row={{
              id,
              type: "chat",
              access: ACCESS.MANAGED,
              managedOn: true,
              ownRoutes: [],
              model: {},
            }}
          />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const queryState = (id) => client.getQueryState(["agentcc-prompts-model", id]);

describe("R2-MODEL-LIST-H6-002 Open in Prompts", () => {
  it.each(laterPageCatalogs)("keeps the action for later-page $id", async ({ id, rows }) => {
    get.mockImplementation(async (_url, options) => modelListResponse(rows, options));
    renderTryIt(id);

    const link = await screen.findByRole("link", { name: "Open in Prompts" });
    expect(link.getAttribute("href")).toContain(`model=${encodeURIComponent(id)}`);
    expect(get).toHaveBeenCalledWith("/model-hub/api/models_list/", expect.objectContaining({
      params: { name: id }, timeout: 15000, signal: expect.any(AbortSignal),
    }));
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("does not enable the action for a substring-only hit", async () => {
    const { id, exact } = laterPageCatalogs[0];
    get.mockResolvedValue({ data: { results: [{ ...exact, model_name: `${id}-mini` }] } });
    renderTryIt(id);
    await waitFor(() => expect(queryState(id)?.status).toBe("success"));
    expect(screen.queryByRole("link", { name: "Open in Prompts" })).not.toBeInTheDocument();
  });

  it("does not enable the action for a truly absent model", async () => {
    get.mockResolvedValue({ data: { results: [] } });
    renderTryIt("azure/missing-model");
    await waitFor(() => expect(queryState("azure/missing-model")?.status).toBe("success"));
    expect(screen.queryByRole("link", { name: "Open in Prompts" })).not.toBeInTheDocument();
  });

  it("says the list could not be checked on lookup failure", async () => {
    get.mockRejectedValue(new Error("unavailable"));
    renderTryIt("azure/gpt-4");
    expect(await screen.findByText(/Could not check Prompts models/)).toBeInTheDocument();
    expect(screen.queryByText(/not in the Prompts model list/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open in Prompts" })).not.toBeInTheDocument();
  });
});
