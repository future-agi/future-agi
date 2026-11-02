import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import {
  fixtureFixRun,
  resetFixRunFixtures,
} from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

const navigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
}));

const { FinishedActions } = await import("../FixFinishedView");

const renderActions = (fix) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SnackbarProvider>
        <MemoryRouter>
          <FinishedActions fix={{ ...fix, environment_id: "env-1" }} onReview={vi.fn()} />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );

describe("Retry on a failed fix", () => {
  beforeEach(() => {
    resetFixRunFixtures();
    navigate.mockReset();
  });

  it("starts the fix again and opens the new one", async () => {
    const user = userEvent.setup();
    renderActions(fixtureFixRun("fix-failed"));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(navigate.mock.calls[0][0]).toMatch(
      /^\/dashboard\/simulate\/environments\/env-1\/improvements\/fix-new-\d+$/,
    );
  });

  it("is not offered once the fix cannot be retried", () => {
    renderActions({ ...fixtureFixRun("fix-failed"), retryable: false });
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("is not offered on a fix that did not fail", () => {
    renderActions(fixtureFixRun("fix-stopped"));
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});
