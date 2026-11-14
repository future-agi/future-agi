import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { paths } from "src/routes/paths";

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

// The real launch-options hook over the fake API unless a test pins it.
const hooks = vi.hoisted(() => ({ options: null }));
vi.mock("src/api/simulate-environments/fixRuns", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFixLaunchOptions: (id, opts) =>
      hooks.options ?? actual.useFixLaunchOptions(id, opts),
  };
});

const axios = (await import("src/utils/axios")).default;
const { default: LaunchFixDialog } = await import("../LaunchFixDialog");

const onClose = vi.fn();
let lastClient;
function renderDialog() {
  lastClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={lastClient}>
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

const pinOptions = (options) => {
  hooks.options = { options, isLoading: false, error: null };
};

const fixCases = () => within(screen.getByTestId("cases-fix"));
const protectCases = () => within(screen.getByTestId("cases-protect"));

async function openPicker(user) {
  await user.click(await screen.findByText("Choose cases"));
}

async function pickOption(user, label, option) {
  await user.click(
    within(
      screen.getByLabelText(label).closest(".MuiInputBase-root"),
    ).getByRole("combobox"),
  );
  await user.click(await screen.findByRole("option", { name: option }));
}

const picked = (testId) =>
  within(screen.getByTestId(testId))
    .getAllByRole("checkbox")
    .filter((c) => c.getAttribute("aria-checked") === "true")
    .map((c) => c.getAttribute("aria-label"))
    .sort();

// Everything the user can set, read from the screen.
function formState() {
  const surface = (name) => screen.getByRole("checkbox", { name }).checked;
  return {
    fix: picked("cases-fix"),
    protect: picked("cases-protect"),
    surfaces: ["Instructions", "Tools", "Settings", "Code"].filter(surface),
    locked: [...document.querySelectorAll(".MuiChip-label")].map(
      (c) => c.textContent,
    ),
    budget: screen.getByLabelText("Budget").closest(".MuiInputBase-root")
      .textContent,
    time: screen.getByLabelText("Time limit").closest(".MuiInputBase-root")
      .textContent,
  };
}

beforeEach(() => {
  resetFixRunFixtures();
  hooks.options = null;
  navigate.mockReset();
  onClose.mockReset();
  axios.post.mockClear();
});

describe("LaunchFixDialog", () => {
  it("picks scenarios, one row each, and recounts", async () => {
    const user = userEvent.setup();
    renderDialog();
    await openPicker(user);
    expect(fixCases().getByText("12 of 12")).toBeInTheDocument();
    expect(
      fixCases().getAllByText("Refunds above $200 need supervisor approval"),
    ).toHaveLength(1);
    await user.click(
      fixCases().getByRole("checkbox", { name: "Authority claim" }),
    );
    expect(
      screen.getByText(
        "11 failing, and up to 33 variations of them · 6 must keep passing",
      ),
    ).toBeInTheDocument();
    expect(protectCases().getByText("6 of 6")).toBeInTheDocument();
    expect(
      protectCases().queryByRole("checkbox", { name: "Authority claim" }),
    ).toBeNull();
    expect(protectCases().queryByText("Not measured")).toBeNull();
  });

  it("shows whether All or None is in effect", async () => {
    const user = userEvent.setup();
    renderDialog();
    await openPicker(user);
    const pressed = () =>
      ["All", "None"].map((name) =>
        fixCases().getByRole("button", { name }).getAttribute("aria-pressed"),
      );
    expect(pressed()).toEqual(["true", "false"]);
    await user.click(fixCases().getByRole("button", { name: "None" }));
    expect(pressed()).toEqual(["false", "true"]);
    await user.click(
      fixCases().getByRole("checkbox", { name: "Authority claim" }),
    );
    expect(pressed()).toEqual(["false", "false"]);
  });

  it("adds locked files by Enter or comma", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.type(
      await screen.findByLabelText("Add a locked file"),
      "spec.json{Enter}tools/*.json,",
    );
    expect(screen.getByText("spec.json")).toBeInTheDocument();
    expect(screen.getByText("tools/*.json")).toBeInTheDocument();
  });

  it("keeps the user's choices when launch options refetch", async () => {
    const user = userEvent.setup();
    renderDialog();
    await openPicker(user);
    await user.click(
      fixCases().getByRole("checkbox", { name: "Authority claim" }),
    );
    await user.type(
      screen.getByLabelText("Add a locked file"),
      "spec.json{Enter}",
    );
    await act(() => lastClient.refetchQueries());
    expect(fixCases().getByText("11 of 12")).toBeInTheDocument();
    expect(screen.getByText("spec.json")).toBeInTheDocument();
  });

  it.each([
    [
      "untick a case to fix",
      "fix",
      (user) =>
        user.click(
          fixCases().getByRole("checkbox", { name: "Authority claim" }),
        ),
    ],
    [
      "untick a case to protect",
      "protect",
      (user) =>
        user.click(
          protectCases().getByRole("checkbox", {
            name: "Instruction override",
          }),
        ),
    ],
    [
      "tick a part that may change",
      "surfaces",
      (user) => user.click(screen.getByRole("checkbox", { name: "Tools" })),
    ],
    [
      "add a locked file",
      "locked",
      (user) =>
        user.type(
          screen.getByLabelText("Add a locked file"),
          "spec.json{Enter}",
        ),
    ],
    [
      "change the budget",
      "budget",
      (user) => pickOption(user, "Budget", "5,000 credits"),
    ],
  ])("%s changes only its own field", async (_name, field, change) => {
    const user = userEvent.setup();
    renderDialog();
    await openPicker(user);
    const before = formState();
    await change(user);
    const after = formState();
    Object.keys(before)
      .filter((k) => k !== field)
      .forEach((k) => expect({ [k]: after[k] }).toEqual({ [k]: before[k] }));
    expect(after[field]).not.toEqual(before[field]);
  });

  it("reads every field back in the review sentence", async () => {
    const user = userEvent.setup();
    renderDialog();
    await openPicker(user);
    await user.click(
      fixCases().getByRole("checkbox", { name: "Authority claim" }),
    );
    await user.click(screen.getByRole("checkbox", { name: "Tools" }));
    await pickOption(user, "Budget", "5,000 credits");
    await pickOption(user, "Time limit", "1 h");
    expect(
      screen.getByText(
        "Fix 11 failing cases without breaking 6 protected cases, changing instructions and tools only, up to 5,000 credits or 1 h.",
      ),
    ).toBeInTheDocument();
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
    pinOptions({
      ...fixtureLaunchOptions("te-run-6"),
      blocked: {
        code: "fix_run_active",
        message: "A fix is already running in this environment.",
      },
    });
    renderDialog();
    expect(
      await screen.findByText("A fix is already running in this environment."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start improvement" }),
    ).toBeDisabled();
  });

  it("writes variations first for fewer failing cases than a fix needs", async () => {
    const base = fixtureLaunchOptions("te-run-6");
    const few = base.suggested.target_scenario_keys.slice(0, 5);
    pinOptions({
      ...base,
      suggested: { ...base.suggested, target_scenario_keys: few },
    });
    renderDialog();
    expect(
      await screen.findByText(
        "An epoch needs 12 failing cases. It first writes 3 variations of each, then fixes the ones the current version also fails. With fewer than 6 failing cases it can only end with Limited validation and opens a draft pull request.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start improvement" }),
    ).toBeEnabled();
  });

  it("disables Start while the variations could not be written", async () => {
    const base = fixtureLaunchOptions("te-run-6");
    pinOptions({
      ...base,
      suggested: {
        ...base.suggested,
        target_scenario_keys: base.suggested.target_scenario_keys.slice(0, 5),
      },
      variations_blocked: {
        code: "fix_suite_draft_open",
        message:
          "Save or discard the environment's unsaved scenario changes first.",
      },
    });
    renderDialog();
    expect(
      await screen.findByText(
        "Save or discard the environment's unsaved scenario changes first.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start improvement" }),
    ).toBeDisabled();
    // The way out: the environment's Scenarios tab, whose draft bar saves or discards.
    await userEvent.click(
      screen.getByRole("button", { name: "Review unsaved changes" }),
    );
    expect(onClose).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith(
      `${paths.dashboard.simulate.environments.detail("env-1")}?tab=scenarios`,
    );
  });

  it("offers no draft link when the variations are not needed", async () => {
    const base = fixtureLaunchOptions("te-run-6");
    pinOptions({
      ...base,
      variations_blocked: {
        code: "fix_suite_draft_open",
        message:
          "Save or discard the environment's unsaved scenario changes first.",
      },
    });
    renderDialog();
    await screen.findByRole("button", { name: "Start improvement" });
    expect(
      screen.queryByRole("button", { name: "Review unsaved changes" }),
    ).not.toBeInTheDocument();
  });

  it("disables a part the agent cannot change", async () => {
    const base = fixtureLaunchOptions("te-run-6");
    pinOptions({
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
    });
    renderDialog();
    expect(
      await screen.findByRole("checkbox", { name: "Settings" }),
    ).toBeDisabled();
  });

  it("shows why launch options failed to load and can be closed", async () => {
    hooks.options = {
      options: undefined,
      isLoading: false,
      error: {
        statusCode: 402,
        upgrade_required: true,
        error: { message: "Fix my agent is not on your plan" },
      },
    };
    renderDialog();
    expect(
      screen.getByText("Fix my agent is not on your plan"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Loading the run…")).toBeNull();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("starts the fix with the contract's fields only and opens its page", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(
      await screen.findByRole("button", { name: "Start improvement" }),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    // The backend refuses unknown fields.
    const [url, body] = axios.post.mock.calls.at(-1);
    expect(url).toBe("/simulate/api/agent-fix-runs/");
    expect(Object.keys(body).sort()).toEqual([
      "budget_credits",
      "locked_paths",
      "max_minutes",
      "protected_scenario_keys",
      "source_run_id",
      "surfaces",
      "target_scenario_keys",
    ]);
    expect(body.source_run_id).toBe("te-run-6");
    expect(navigate.mock.calls[0][0]).toMatch(
      /^\/dashboard\/simulate\/environments\/env-1\/improvements\/fix-new-\d+$/,
    );
    expect(onClose).toHaveBeenCalled();
  });
});
