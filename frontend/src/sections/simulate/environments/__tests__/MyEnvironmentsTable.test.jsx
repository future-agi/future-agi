import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { RUN_SIMULATION_COPY } from "../environmentOptions";

const enqueueSnackbar = vi.fn();
const navigate = vi.fn();

vi.mock("notistack", () => ({ enqueueSnackbar: (...a) => enqueueSnackbar(...a) }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const { default: MyEnvironmentsTab } = await import("../MyEnvironmentsTab");

const renderTab = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MyEnvironmentsTab />
    </QueryClientProvider>,
  );
};

const rowFor = (name) => screen.getByText(name).closest('[role="row"]');

const openMenu = async (user, name) => {
  await user.click(
    within(rowFor(name)).getByRole("button", { name: "Row actions" }),
  );
};

describe("MyEnvironmentsTable", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-15T12:00:00Z"));
    enqueueSnackbar.mockReset();
    navigate.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders every environment with a relative updated time", async () => {
    renderTab();

    expect(await screen.findByText("Customer Support Line")).toBeInTheDocument();
    [
      "Billing Chat Agent",
      "Repo Triage Bot",
      "Browser Checkout Flow",
      "Airline Rebooking",
      "Onboarding Assistant",
    ].forEach((name) => expect(screen.getByText(name)).toBeInTheDocument());

    expect(
      within(rowFor("Customer Support Line")).getByText("3 hours ago"),
    ).toBeInTheDocument();
    expect(
      within(rowFor("Browser Checkout Flow")).getByText("just now"),
    ).toBeInTheDocument();
    expect(
      within(rowFor("Airline Rebooking")).getByText("1 month ago"),
    ).toBeInTheDocument();
  });

  it("shows each status pill once", async () => {
    renderTab();
    await screen.findByText("Customer Support Line");

    ["Passed", "Failed", "Not run yet", "Running…", "Completed", "Building"].forEach(
      (label) => expect(screen.getByText(label)).toBeInTheDocument(),
    );
  });

  it("labels the agent-type cells", async () => {
    renderTab();
    await screen.findByText("Customer Support Line");

    expect(screen.getAllByText("Voice")).toHaveLength(2);
    expect(screen.getAllByText("Chat")).toHaveLength(2);
    expect(screen.getByText("Code")).toBeInTheDocument();
    expect(screen.getByText("Computer use")).toBeInTheDocument();
  });

  it("offers Re-run + Delete (no Open) on a run environment", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");

    const menu = screen.getByRole("menu");
    // Both actions are reachable menuitems: the enabled Run item is a direct
    // child of the menu (not buried in a tooltip wrapper) so it stays focusable.
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(2);
    expect(
      within(menu).getByRole("menuitem", { name: /Re-run simulation/ }),
    ).toBeInTheDocument();
    expect(within(menu).getByText("Delete")).toBeInTheDocument();
    expect(within(menu).queryByText("Open")).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("labels the action Run simulation when the environment has never run", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Repo Triage Bot");

    await openMenu(user, "Repo Triage Bot");

    expect(
      within(screen.getByRole("menu")).getByText("Run simulation"),
    ).toBeInTheDocument();
  });

  it("queues a run and snackbars when Re-run is chosen", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");
    await user.click(
      within(screen.getByRole("menu")).getByText("Re-run simulation"),
    );

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(RUN_SIMULATION_COPY),
    );
  });

  it("disables the run action while the environment is building", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Onboarding Assistant");

    await openMenu(user, "Onboarding Assistant");

    expect(
      within(screen.getByRole("menu"))
        .getByText("Run simulation")
        .closest('[role="menuitem"]'),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("removes the environment after confirming delete", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");
    await user.click(within(screen.getByRole("menu")).getByText("Delete"));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Delete environment?")).toBeInTheDocument();
    expect(within(dialog).getByText("Customer Support Line")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(screen.queryByText("Customer Support Line")).toBeNull(),
    );
    [
      "Billing Chat Agent",
      "Repo Triage Bot",
      "Browser Checkout Flow",
      "Airline Rebooking",
      "Onboarding Assistant",
    ].forEach((name) => expect(screen.getByText(name)).toBeInTheDocument());
  });

  it("keeps the environment when delete is cancelled", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await openMenu(user, "Customer Support Line");
    await user.click(within(screen.getByRole("menu")).getByText("Delete"));

    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }),
    );

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("Customer Support Line")).toBeInTheDocument();
  });

  it("navigates to the detail route on row click", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByText("Customer Support Line");

    await user.click(screen.getByText("Customer Support Line"));

    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/env-support-line",
    );
  });
});
