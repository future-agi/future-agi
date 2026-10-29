import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import { resetFixRunFixtures } from "src/api/simulate-environments/_fixtures/fixRuns";

import FixRunPage from "../FixRunPage";

function renderPage(fixId) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <MemoryRouter>
          <FixRunPage envId="env-1" fixId={fixId} />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
}

describe("FixRunPage header", () => {
  beforeEach(() => resetFixRunFixtures());

  it("names the fix and its agent × environment", async () => {
    renderPage("fix-search");
    expect(await screen.findByText("Improvement 13")).toBeInTheDocument();
    expect(
      screen.getByText("agent v8 × environment v1 · new env"),
    ).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Screen · attempt 2")).toBeInTheDocument();
  });

  it("goes back to the Improvements tab", async () => {
    renderPage("fix-search");
    expect(
      await screen.findByRole("link", { name: "Back to improvements" }),
    ).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1?tab=improvements",
    );
  });

  it("stops a running fix after confirming", async () => {
    const user = userEvent.setup();
    renderPage("fix-search");
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("Stop Improvement 13?"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "2 of up to 4 trials have run · 412 credits spent so far.",
      ),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Stopping" })).toBeDisabled(),
    );
  });

  it("keeps running when the user backs out", async () => {
    const user = userEvent.setup();
    renderPage("fix-search");
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
  });

  it("shows the engine's stage only while the fix runs", async () => {
    renderPage("fix-out-of-budget");
    expect(await screen.findByText("Stopped at budget")).toBeInTheDocument();
    expect(screen.queryByText("Ship")).toBeNull();
  });

  it("offers no Stop on a finished fix", async () => {
    renderPage("fix-ready-archive");
    expect(await screen.findByText("Improvement 10")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("never prints null for unknown identity", async () => {
    const { container } = renderPage("fix-unknown-identity");
    expect(await screen.findByText("Improvement")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/null|undefined/);
  });
});
