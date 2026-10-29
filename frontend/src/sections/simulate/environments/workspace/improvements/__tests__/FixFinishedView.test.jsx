import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import {
  resetFixRunFixtures,
  updateFixtureFixRun,
} from "src/api/simulate-environments/_fixtures/fixRuns";

let shipOverride = null;
vi.mock("src/api/simulate-environments/fixRuns", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useShipCandidate: (fix) =>
      shipOverride ? shipOverride : actual.useShipCandidate(fix),
  };
});

const { default: FixRunPage } = await import("../FixRunPage");

function renderPage(fixId) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <MemoryRouter>
          <FixRunPage envId="env-1" fixId={fixId} />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

describe("finished fix", () => {
  beforeEach(() => {
    resetFixRunFixtures();
    shipOverride = null;
  });

  it("leads with the held-back headline and the three cards", async () => {
    renderPage("fix-ready-archive");
    expect(
      await screen.findByText(
        "Fixed 2 of 2 failing scenarios on the held-back set, broke 1.",
      ),
    ).toBeInTheDocument();
    const cards = within(screen.getByTestId("verdict-cards"));
    expect(cards.getByText("Held back")).toBeInTheDocument();
    expect(cards.getByText("17% → 83%")).toBeInTheDocument();
    expect(cards.getByText("▲ 66 pts")).toBeInTheDocument();
    expect(cards.getByText("Target")).toBeInTheDocument();
    expect(cards.getByText("Overfit gap")).toBeInTheDocument();
    expect(cards.getByText("17 pts")).toBeInTheDocument();
  });

  it("says it was proven on the targets only when nothing was held back", async () => {
    renderPage("fix-ready-no-held");
    expect(
      await screen.findByText("Proven on the targets only"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Held back")).toBeNull();
    expect(screen.queryByText("Overfit gap")).toBeNull();
  });

  it("shows the checks line and every tier on demand", async () => {
    const user = userEvent.setup();
    renderPage("fix-ready-archive");
    expect(
      await screen.findByText("Checks · 5 of 5 passed"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getByText("3 repeats agree")).toBeInTheDocument();
  });

  it("lists must-pass breaks, noting what was within noise", async () => {
    renderPage("fix-ready-archive");
    const breaks = within(await screen.findByTestId("must-pass-breaks"));
    expect(breaks.getByText("on-time-cancel")).toBeInTheDocument();
    expect(breaks.getByText(/refund-window/)).toBeInTheDocument();
    expect(
      breaks.getByText(/within normal noise, not counted/),
    ).toBeInTheDocument();
  });

  it("makes an uploaded agent's winner the default", async () => {
    const user = userEvent.setup();
    renderPage("fix-ready-archive");
    await user.click(
      await screen.findByRole("button", { name: "Make default" }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Default" })).toBeDisabled(),
    );
  });

  it("opens a pull request for a GitHub agent's winner", async () => {
    const user = userEvent.setup();
    renderPage("fix-ready-github");
    await user.click(
      await screen.findByRole("button", { name: "Open pull request" }),
    );
    expect(
      await screen.findByRole("link", { name: "View pull request" }),
    ).toHaveAttribute("href", "https://github.com/acme/dispatch-agent/pull/42");
  });

  it("shows the server's message when the version cannot be activated", async () => {
    const user = userEvent.setup();
    shipOverride = {
      isPending: false,
      mutateAsync: vi.fn().mockRejectedValue({
        statusCode: 409,
        error: "fix_candidate_not_activated",
        message: "Could not activate v11",
      }),
    };
    renderPage("fix-ready-archive");
    await user.click(
      await screen.findByRole("button", { name: "Make default" }),
    );
    expect(
      await screen.findByText("Could not activate v11"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make default" })).toBeEnabled();
  });

  it("offers the patch when the repository cannot take a pull request", async () => {
    const user = userEvent.setup();
    shipOverride = {
      isPending: false,
      mutateAsync: vi.fn().mockRejectedValue({
        statusCode: 422,
        error: "fix_github_not_connected",
        message: "Not connected through the GitHub App",
      }),
    };
    renderPage("fix-ready-github");
    await user.click(
      await screen.findByRole("button", { name: "Open pull request" }),
    );
    expect(
      await screen.findByRole("button", { name: "Download patch" }),
    ).toBeInTheDocument();
  });

  it("explains how it was decided", async () => {
    const user = userEvent.setup();
    renderPage("fix-ready-archive");
    await user.click(
      await screen.findByRole("button", { name: "How it was decided" }),
    );
    const drawer = await screen.findByTestId("fix-decision");
    expect(within(drawer).getByText("Trial 1 · Rejected")).toBeInTheDocument();
    expect(
      within(drawer).getByText("target: 0 of 1 target scenarios passed"),
    ).toBeInTheDocument();
  });

  it("shows why a failed fix ended and offers no Ship", async () => {
    renderPage("fix-failed");
    expect(
      await screen.findByText(
        "The engine could not start the agent's sandbox.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Make default|Open pull request/ }),
    ).toBeNull();
  });

  it("switches to the finished view when a running fix ends", async () => {
    const { client } = renderPage("fix-verify");
    expect(await screen.findByTestId("fix-running-view")).toBeInTheDocument();
    updateFixtureFixRun("fix-verify", (f) => ({
      ...f,
      state: "ready",
      step: "done",
      candidates: f.candidates.map((c) =>
        c.outcome === "pending" ? { ...c, outcome: "winner" } : c,
      ),
    }));
    await client.refetchQueries();
    expect(await screen.findByTestId("fix-finished-view")).toBeInTheDocument();
    expect(screen.queryByTestId("fix-running-view")).toBeNull();
  });
});
