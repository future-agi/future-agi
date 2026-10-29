import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import {
  fixtureLaunchOptions,
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

let optionsOverride = null;
vi.mock("src/api/simulate-environments/fixRuns", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFixLaunchOptions: (id, opts) =>
      optionsOverride
        ? { options: optionsOverride, isLoading: false }
        : actual.useFixLaunchOptions(id, opts),
  };
});

const { default: LaunchFixDialog } = await import("../LaunchFixDialog");

const onClose = vi.fn();
let lastClient;
function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  lastClient = client;
  return render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <MemoryRouter>
          <LaunchFixDialog
            open
            envId="env-1"
            sourceRunId="te-run-6"
            onClose={onClose}
          />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
}

describe("LaunchFixDialog", () => {
  beforeEach(() => {
    resetFixRunFixtures();
    optionsOverride = null;
    navigate.mockReset();
    onClose.mockReset();
  });

  it("names the agent and reads the suggested plan back", async () => {
    renderDialog();
    expect(
      await screen.findByText("Improve Customer Support Line agent"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Searches for a better Customer Support Line agent, scored by this environment",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Fix 12 failing cases without breaking 6 protected cases, changing instructions only, up to 1,500 credits or 2 h 30 min.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Customer Support Line agent · agent v1 · main @ 0487e25 × environment v1",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "8 failing to fix · 4 held back to prove it · 6 must keep passing",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("About 4 held back to prove the fix"),
    ).toBeInTheDocument();
  });

  it("shows only what the contract has", async () => {
    renderDialog();
    await screen.findByText("Improve Customer Support Line agent");
    expect(
      screen.getByText("Stops at the limit with the best it found."),
    ).toBeInTheDocument();
    ["Copy as command", "More options", "Show breakdown"].forEach((t) =>
      expect(screen.queryByText(t)).toBeNull(),
    );
    expect(screen.queryByText(/full plan would need/)).toBeNull();
  });

  it("picks scenarios, one row each, and recounts", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByText("Choose cases"));
    const fix = within(screen.getByTestId("cases-fix"));
    expect(fix.getByText("12 of 12")).toBeInTheDocument();
    expect(
      fix.getAllByText("Refunds above $200 need supervisor approval"),
    ).toHaveLength(1);
    expect(fix.queryByText("Held back")).toBeNull();
    expect(fix.queryByText(/Flaky/)).toBeNull();
    await user.click(fix.getByRole("checkbox", { name: "Authority claim" }));
    expect(
      screen.getByText("11 failing to fix · 6 must keep passing"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Too few failing cases to hold some back. The fix will be proven on the picked cases only.",
      ),
    ).toBeInTheDocument();
    const protect = within(screen.getByTestId("cases-protect"));
    expect(protect.getAllByText("Not measured")).toHaveLength(2);
  });

  it("shows whether All or None is in effect", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByText("Choose cases"));
    const fix = within(screen.getByTestId("cases-fix"));
    expect(fix.getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(fix.getByRole("button", { name: "None" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await user.click(fix.getByRole("button", { name: "None" }));
    expect(fix.getByRole("button", { name: "None" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(fix.getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await user.click(fix.getByRole("checkbox", { name: "Authority claim" }));
    expect(fix.getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(fix.getByRole("button", { name: "None" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("adds a locked file without touching the picked cases", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByText("Choose cases"));
    await user.type(
      screen.getByLabelText("Add a locked file"),
      "spec.json{Enter}",
    );
    expect(screen.getByText("spec.json")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("cases-fix")).getByText("12 of 12"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start improvement" }),
    ).toBeEnabled();
  });

  it("keeps the user's choices when launch options refetch", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByText("Choose cases"));
    await user.click(
      within(screen.getByTestId("cases-fix")).getByRole("checkbox", {
        name: "Authority claim",
      }),
    );
    await user.type(
      screen.getByLabelText("Add a locked file"),
      "spec.json{Enter}",
    );
    await act(() => lastClient.refetchQueries());
    expect(
      within(screen.getByTestId("cases-fix")).getByText("11 of 12"),
    ).toBeInTheDocument();
    expect(screen.getByText("spec.json")).toBeInTheDocument();
  });

  it("explains locked files and adds several paths, by Enter or comma", async () => {
    const user = userEvent.setup();
    renderDialog();
    const input = await screen.findByLabelText("Add a locked file");
    expect(input).toHaveAttribute(
      "placeholder",
      "Files or globs it must never edit, like prompts/*.md. Press Enter to add each.",
    );
    await user.type(input, "spec.json{Enter}tools/*.json,");
    expect(screen.getByText("spec.json")).toBeInTheDocument();
    expect(screen.getByText("tools/*.json")).toBeInTheDocument();
    expect(input).toHaveAttribute(
      "placeholder",
      "Add another, then press Enter",
    );
  });

  it("disables Start with the reason when nothing may change", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(
      await screen.findByRole("checkbox", { name: "Instructions" }),
    );
    expect(
      screen.getByRole("button", { name: "Start improvement" }),
    ).toBeDisabled();
    expect(
      screen.getByText("Pick at least one part it may change."),
    ).toBeInTheDocument();
  });

  it("disables Start when the platform blocks the fix", async () => {
    optionsOverride = {
      ...fixtureLaunchOptions("te-run-6"),
      blocked: {
        code: "fix_run_active",
        message: "A fix is already running in this environment.",
      },
    };
    renderDialog();
    expect(
      await screen.findByText("A fix is already running in this environment."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start improvement" }),
    ).toBeDisabled();
  });

  it("shows a part the agent cannot change as disabled with its reason", async () => {
    const base = fixtureLaunchOptions("te-run-6");
    optionsOverride = {
      ...base,
      surfaces: base.surfaces.map((s) =>
        s.id === "settings"
          ? {
              ...s,
              allowed: false,
              reason: "Settings live on the hosting platform.",
            }
          : s,
      ),
    };
    renderDialog();
    expect(
      await screen.findByRole("checkbox", { name: "Settings" }),
    ).toBeDisabled();
  });

  it("starts the fix and opens its page", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(
      await screen.findByRole("button", { name: "Start improvement" }),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(navigate.mock.calls[0][0]).toMatch(
      /^\/dashboard\/simulate\/environments\/env-1\/improvements\/fix-new-\d+$/,
    );
    expect(onClose).toHaveBeenCalled();
  });
});
