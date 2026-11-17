import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import ConfigHistoryDrawer from "./ConfigHistoryDrawer";

// Restoring a version never switches daily caps on without the person
// choosing what happens: over caps off, a version with caps on is offered
// with caps off; over caps on, restoring caps off says it stops them.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { get, activate, history } = vi.hoisted(() => ({
  get: vi.fn(),
  activate: vi.fn(),
  history: { current: { data: [], isLoading: false } },
}));
vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfigHistory: () => history.current,
  useActivateOrgConfig: () => ({ mutate: activate, isPending: false }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    gateway: {
      orgConfig: { diff: (id) => `/agentcc/org-configs/${id}/diff/` },
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const RESTORE_OFF =
  "This version has daily caps on. Daily caps can't be turned on in this release. Restore it with daily caps off?";
const STOPS =
  "This version has daily caps off. Restoring it stops daily cap enforcement.";
const EMPTY = { added: {}, removed: {}, changed: {} };

const version = (id, number, active, budgets) => ({
  id,
  version: number,
  is_active: active,
  budgets,
});
const ON = { enabled: true, admission_caps_enabled: true, policy_revision: 3 };
const ON_ALIAS = {
  enabled: true,
  admissionCapsEnabled: true,
  policyRevision: 3,
};
const OFF = {
  enabled: true,
  admission_caps_enabled: false,
  policy_revision: 3,
};

let client;
function renderDrawer(versions) {
  history.current = { data: versions, isLoading: false };
  return render(
    <QueryClientProvider client={client}>
      <ConfigHistoryDrawer open onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  activate.mockReset();
  get.mockReset();
  get.mockResolvedValue({
    data: { result: { from_version: 4, to_version: 2, routing: EMPTY } },
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

describe("restoring a version with daily caps", () => {
  it.each([
    ["canonical", ON],
    ["alias-only", ON_ALIAS],
  ])(
    "%s caps on over caps off: offered with caps off, by explicit choice",
    async (_name, budgets) => {
      const user = userEvent.setup();
      renderDrawer([
        version("cfg-4", 4, true, OFF),
        version("cfg-2", 2, false, budgets),
      ]);
      await user.click(screen.getByRole("button", { name: "Activate" }));

      const text = await screen.findByText(RESTORE_OFF);
      const dialog = text.closest('[role="dialog"]');
      expect(within(dialog).getByText("Restore version 2")).toBeInTheDocument();
      expect(
        within(dialog).getByRole("button", { name: "Restore with caps off" }),
      ).toBeEnabled();
      // The diff of a version that can't be restored as it is isn't offered.
      expect(screen.queryByRole("button", { name: "Activate v2" })).toBeNull();
      expect(get).not.toHaveBeenCalled();

      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      expect(activate).not.toHaveBeenCalled();

      // The drawer is usable again once the dialog has closed.
      await user.click(await screen.findByRole("button", { name: "Activate" }));
      await user.click(
        await screen.findByRole("button", { name: "Restore with caps off" }),
      );
      expect(activate).toHaveBeenCalledTimes(1);
      expect(activate).toHaveBeenCalledWith("cfg-2", expect.any(Object));
    },
  );

  it("caps off over caps on: the confirm says restoring stops daily caps", async () => {
    const user = userEvent.setup();
    renderDrawer([
      version("cfg-4", 4, true, ON),
      version("cfg-2", 2, false, OFF),
    ]);
    await user.click(screen.getByRole("button", { name: "Activate" }));

    expect(await screen.findByTestId("activate-caps-notice")).toHaveTextContent(
      STOPS,
    );
    expect(screen.queryByText(RESTORE_OFF)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Activate v2" }));
    expect(activate).toHaveBeenCalledWith("cfg-2", expect.any(Object));
  });

  it.each([
    ["caps on over caps on", ON, ON],
    ["caps off over caps off", OFF, OFF],
    ["no caps fields", {}, {}],
  ])("%s: the usual confirm, with no caps sentence", async (_n, a, t) => {
    const user = userEvent.setup();
    renderDrawer([version("cfg-4", 4, true, a), version("cfg-2", 2, false, t)]);
    await user.click(screen.getByRole("button", { name: "Activate" }));

    expect(
      await screen.findByRole("button", { name: "Activate v2" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("activate-caps-notice")).toBeNull();
    expect(screen.queryByText(RESTORE_OFF)).toBeNull();
  });
});
