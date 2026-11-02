import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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

const axios = (await import("src/utils/axios")).default;
const { default: ShipButton } = await import("../ShipButton");

const renderShip = (fix) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SnackbarProvider>
        <MemoryRouter>
          <ShipButton fix={{ ...fix, environment_id: "env-1" }} />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );

const shipPosts = () =>
  axios.post.mock.calls.filter(([url]) =>
    /make-default|pull-request/.test(url),
  );

describe("Ship asks before it acts", () => {
  beforeEach(() => {
    resetFixRunFixtures();
    axios.post.mockClear();
  });

  it("shows where a GitHub agent's pull request goes and what it changes, before opening it", async () => {
    const user = userEvent.setup();
    renderShip(fixtureFixRun("fix-ready-github"));
    await user.click(screen.getByRole("button", { name: "Ship" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Open a pull request for Trial 3?")).toBeInTheDocument();
    expect(within(dialog).getByText("main @ 0487e25")).toBeInTheDocument();
    expect(await within(dialog).findByText("instructions.md")).toBeInTheDocument();
    expect(within(dialog).getByText("+3 −1")).toBeInTheDocument();
    expect(within(dialog).queryByText("Repo")).toBeNull();
    expect(shipPosts()).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Open pull request" }));
    await waitFor(() => expect(shipPosts()).toHaveLength(1));
    expect(shipPosts()[0][0]).toMatch(/pull-request\/$/);
  });

  it("names the repository when the backend sends it", async () => {
    const user = userEvent.setup();
    const fix = fixtureFixRun("fix-ready-github");
    fix.agent.repository = "acme/dispatch-agent";
    renderShip(fix);
    await user.click(screen.getByRole("button", { name: "Ship" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Repo")).toBeInTheDocument();
    expect(within(dialog).getByText("acme/dispatch-agent")).toBeInTheDocument();
  });

  it("says a limited fix opens as a draft", async () => {
    const user = userEvent.setup();
    renderShip({ ...fixtureFixRun("fix-ready-github"), limited_validation: true });
    await user.click(screen.getByRole("button", { name: "Ship" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(
        "Limited validation: it opens as a draft pull request, marked Limited validation in its title and body.",
      ),
    ).toBeInTheDocument();
  });

  it("asks before making an uploaded agent's winner the default", async () => {
    const user = userEvent.setup();
    renderShip(fixtureFixRun("fix-ready-archive"));
    await user.click(screen.getByRole("button", { name: "Ship" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Make Trial 3's version the default?")).toBeInTheDocument();
    expect(shipPosts()).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Make default" }));
    await waitFor(() => expect(shipPosts()).toHaveLength(1));
    expect(shipPosts()[0][0]).toMatch(/make-default\/$/);
  });

  it("does nothing when the user cancels", async () => {
    const user = userEvent.setup();
    renderShip(fixtureFixRun("fix-ready-github"));
    await user.click(screen.getByRole("button", { name: "Ship" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(shipPosts()).toHaveLength(0);
  });
});
