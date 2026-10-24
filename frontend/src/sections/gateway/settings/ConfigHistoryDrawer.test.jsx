import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import ConfigHistoryDrawer from "./ConfigHistoryDrawer";

// The write controls are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. AdminGating.test.jsx covers the other levels.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { get, snackbar } = vi.hoisted(() => ({
  get: vi.fn(),
  snackbar: vi.fn(),
}));

const mockActivate = vi.fn();

let mockHistoryReturn = {
  data: [],
  isLoading: false,
};

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfigHistory: () => mockHistoryReturn,
  useActivateOrgConfig: () => ({ mutate: mockActivate, isPending: false }),
}));

vi.mock("src/utils/axios", () => ({
  default: { get },
  endpoints: {
    gateway: {
      orgConfig: {
        diff: (id) => `/agentcc/org-configs/${id}/diff/`,
      },
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: snackbar }));

const EMPTY = { added: {}, removed: {}, changed: {} };

let client;
function renderDrawer() {
  return render(
    <QueryClientProvider client={client}>
      <ConfigHistoryDrawer open onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("ConfigHistoryDrawer", () => {
  beforeEach(() => {
    mockActivate.mockReset();
    get.mockReset();
    snackbar.mockReset();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mockHistoryReturn = {
      data: [],
      isLoading: false,
    };
  });

  it("renders snake_case history metadata and hides activate for active config", async () => {
    const user = userEvent.setup();
    const createdAt = "2026-04-14T08:34:37.245253Z";

    mockHistoryReturn = {
      data: [
        {
          id: "cfg-1",
          version: 48,
          is_active: true,
          created_at: createdAt,
          change_description: "Update guardrail pii-detector",
          guardrails: { rules: [] },
          routing: {},
          cache: {},
        },
      ],
      isLoading: false,
    };

    renderDrawer();

    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(
      screen.getByText(new Date(createdAt).toLocaleDateString()),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Update guardrail pii-detector"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /activate/i }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /view/i }));

    expect(screen.queryByText(/"providers"/)).not.toBeInTheDocument();
  });

  describe("activating an older version", () => {
    beforeEach(() => {
      mockHistoryReturn = {
        data: [
          {
            id: "cfg-4",
            version: 4,
            is_active: true,
            managed_models: { enabled: true },
          },
          { id: "cfg-2", version: 2, is_active: false, managed_models: {} },
        ],
        isLoading: false,
      };
      get.mockResolvedValue({
        data: {
          result: {
            from_version: 4,
            to_version: 2,
            routing: EMPTY,
            managed_models: {
              added: {},
              removed: { enabled: true },
              changed: {},
            },
          },
        },
      });
    });

    it("shows the diff against the active version and the managed-models warning before activating", async () => {
      const user = userEvent.setup();
      renderDrawer();

      await user.click(screen.getByRole("button", { name: "Activate" }));

      expect(
        await screen.findByText(/turns managed models off/i),
      ).toBeInTheDocument();
      expect(get).toHaveBeenCalledWith("/agentcc/org-configs/cfg-2/diff/", {
        params: { compare_to: "cfg-4" },
      });
      expect(mockActivate).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: "Activate v2" }));

      expect(mockActivate).toHaveBeenCalledWith("cfg-2", expect.any(Object));
    });

    it("cancelling the diff activates nothing", async () => {
      const user = userEvent.setup();
      renderDrawer();

      await user.click(screen.getByRole("button", { name: "Activate" }));
      await screen.findByText(/turns managed models off/i);
      await user.click(screen.getByRole("button", { name: "Cancel" }));

      await waitFor(() =>
        expect(
          screen.queryByText(/turns managed models off/i),
        ).not.toBeInTheDocument(),
      );
      expect(mockActivate).not.toHaveBeenCalled();
    });

    it("reports the server's warnings and re-reads the gateway config after activating", async () => {
      const user = userEvent.setup();
      const invalidate = vi.spyOn(client, "invalidateQueries");
      mockActivate.mockImplementation((_, { onSuccess }) =>
        onSuccess({
          version: 2,
          warnings: [{ code: "managed_models_change", from: true, to: false }],
        }),
      );
      renderDrawer();

      await user.click(screen.getByRole("button", { name: "Activate" }));
      await screen.findByText(/turns managed models off/i);
      await user.click(screen.getByRole("button", { name: "Activate v2" }));

      expect(snackbar).toHaveBeenCalledWith(
        expect.stringMatching(/managed models are now off/i),
        expect.objectContaining({ variant: "warning" }),
      );
      const keys = invalidate.mock.calls.map(([arg]) => arg.queryKey[0]);
      expect(keys).toContain("agentcc-gateway-config");
      await waitFor(() =>
        expect(
          screen.queryByText(/turns managed models off/i),
        ).not.toBeInTheDocument(),
      );
    });
  });
});
