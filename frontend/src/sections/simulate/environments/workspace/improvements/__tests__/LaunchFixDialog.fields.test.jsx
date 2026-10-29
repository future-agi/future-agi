import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";

import { resetFixRunFixtures } from "src/api/simulate-environments/_fixtures/fixRuns";

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
}));

const { default: LaunchFixDialog } = await import("../LaunchFixDialog");

function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SnackbarProvider>
        <MemoryRouter>
          <LaunchFixDialog
            open
            envId="env-1"
            sourceRunId="te-run-6"
            onClose={vi.fn()}
          />
        </MemoryRouter>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
}

const picked = (testId) =>
  within(screen.getByTestId(testId))
    .getAllByRole("checkbox")
    .filter((c) => c.getAttribute("aria-checked") === "true")
    .map((c) => c.getAttribute("aria-label"))
    .sort();

// Everything the user can set, read from the screen.
function state() {
  const surface = (name) => screen.getByRole("checkbox", { name }).checked;
  return {
    fix: picked("cases-fix"),
    protect: picked("cases-protect"),
    fixCount: within(screen.getByTestId("cases-fix")).getByText(/^\d+ of \d+$/)
      .textContent,
    protectCount: within(screen.getByTestId("cases-protect")).getByText(
      /^\d+ of \d+$/,
    ).textContent,
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

// Each change touches one field; every other field must read the same after it.
const CHANGES = [
  [
    "untick a case to fix",
    "fix",
    async (user) =>
      user.click(
        within(screen.getByTestId("cases-fix")).getByRole("checkbox", {
          name: "Authority claim",
        }),
      ),
  ],
  [
    "untick a case to protect",
    "protect",
    async (user) =>
      user.click(
        within(screen.getByTestId("cases-protect")).getByRole("checkbox", {
          name: "Instruction override",
        }),
      ),
  ],
  [
    "tick a not-measured case to protect",
    "protect",
    async (user) =>
      user.click(
        within(screen.getByTestId("cases-protect")).getByRole("checkbox", {
          name: "Stale record",
        }),
      ),
  ],
  [
    "tick a part that may change",
    "surfaces",
    async (user) => user.click(screen.getByRole("checkbox", { name: "Tools" })),
  ],
  [
    "untick the default part",
    "surfaces",
    async (user) =>
      user.click(screen.getByRole("checkbox", { name: "Instructions" })),
  ],
  [
    "add a locked file with Enter",
    "locked",
    async (user) =>
      user.type(screen.getByLabelText("Add a locked file"), "spec.json{Enter}"),
  ],
  [
    "add a locked file with a comma",
    "locked",
    async (user) =>
      user.type(screen.getByLabelText("Add a locked file"), "tools/*.json,"),
  ],
  [
    "change the budget",
    "budget",
    async (user) => pickOption(user, "Budget", "5,000 credits"),
  ],
  [
    "change the time limit",
    "time",
    async (user) => pickOption(user, "Time limit", "1 h"),
  ],
  [
    "search the cases to fix (hides rows, keeps the picks)",
    "fix",
    async (user) =>
      user.type(screen.getByLabelText("Search Cases to fix"), "refund"),
  ],
  [
    "press None on cases to protect",
    "protect",
    async (user) =>
      user.click(
        within(screen.getByTestId("cases-protect")).getByRole("button", {
          name: "None",
        }),
      ),
  ],
  [
    "press All on cases to protect",
    "protect",
    async (user) =>
      user.click(
        within(screen.getByTestId("cases-protect")).getByRole("button", {
          name: "All",
        }),
      ),
  ],
  [
    "hide and reopen the cases",
    null,
    async (user) => {
      await user.click(screen.getByText("Hide cases"));
      await user.click(screen.getByText("Choose cases"));
    },
  ],
];

describe("LaunchFixDialog fields stay independent", () => {
  beforeEach(() => resetFixRunFixtures());

  it.each(CHANGES)(
    "%s changes only its own field",
    async (name, field, change) => {
      const searching = name.startsWith("search");
      const user = userEvent.setup();
      renderDialog();
      await openPicker(user);
      const before = state();
      await change(user);
      const after = state();
      Object.keys(before)
        // A case unticked from Fix becomes protectable, so the protect total grows.
        .filter(
          (k) =>
            k !== field &&
            !(k === `${field}Count` && !searching) &&
            !(field === "fix" && !searching && k === "protectCount"),
        )
        .forEach((k) => expect({ [k]: after[k] }).toEqual({ [k]: before[k] }));
      if (field) expect(after[field]).not.toEqual(before[field]);
    },
  );

  it("unticking a case to fix moves it to the protect list without ticking it", async () => {
    const user = userEvent.setup();
    renderDialog();
    await openPicker(user);
    const protectBefore = picked("cases-protect");
    await user.click(
      within(screen.getByTestId("cases-fix")).getByRole("checkbox", {
        name: "Authority claim",
      }),
    );
    expect(
      within(screen.getByTestId("cases-protect")).getByRole("checkbox", {
        name: "Authority claim",
      }),
    ).toHaveAttribute("aria-checked", "false");
    expect(picked("cases-protect")).toEqual(protectBefore);
  });

  it("re-ticking a case to fix takes it off the protect list even if it was protected", async () => {
    const user = userEvent.setup();
    renderDialog();
    await openPicker(user);
    const fix = within(screen.getByTestId("cases-fix"));
    await user.click(fix.getByRole("checkbox", { name: "Authority claim" }));
    await user.click(
      within(screen.getByTestId("cases-protect")).getByRole("checkbox", {
        name: "Authority claim",
      }),
    );
    await user.click(fix.getByRole("checkbox", { name: "Authority claim" }));
    expect(
      within(screen.getByTestId("cases-protect")).queryByRole("checkbox", {
        name: "Authority claim",
      }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Start improvement" }),
    ).toBeEnabled();
  });

  it("the review sentence follows every field", async () => {
    const user = userEvent.setup();
    renderDialog();
    await openPicker(user);
    await user.click(
      within(screen.getByTestId("cases-fix")).getByRole("checkbox", {
        name: "Authority claim",
      }),
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
});
