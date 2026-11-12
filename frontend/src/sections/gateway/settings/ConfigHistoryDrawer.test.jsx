import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import ConfigHistoryDrawer from "./ConfigHistoryDrawer";

// The write controls are Owner or Admin only (useGatewayAdmin); these cases
// act as an Admin. RelfupAdminGating.test.jsx covers the other levels.
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
function renderDrawer(onClose = vi.fn()) {
  return render(
    <QueryClientProvider client={client}>
      <ConfigHistoryDrawer open onClose={onClose} />
    </QueryClientProvider>,
  );
}

describe("ConfigHistoryDrawer", () => {
  beforeEach(() => {
    mockActivate.mockReset();
    get.mockReset();
    snackbar.mockReset();
    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    mockHistoryReturn = {
      data: [],
      isLoading: false,
    };
  });

  it("retries a first-read failure without showing empty history or activating a version", async () => {
    const refetch = vi.fn();
    const onClose = vi.fn();
    mockHistoryReturn = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: { statusCode: 500 },
      refetch,
    };
    const { rerender } = renderDrawer(onClose);
    expect(screen.getByText(/Could not load history/)).toBeInTheDocument();
    expect(screen.queryByText(/No config history/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(mockActivate).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    mockHistoryReturn = {
      data: [{ id: "cfg-7", version: 7, is_active: true }],
      isSuccess: true,
    };
    rerender(
      <QueryClientProvider client={client}>
        <ConfigHistoryDrawer open onClose={onClose} />
      </QueryClientProvider>,
    );
    expect(
      screen.getByRole("dialog", { name: "Config History" }),
    ).toBeInTheDocument();
    expect(screen.getByText("v7")).toBeInTheDocument();
    expect(
      screen.queryByText(/Could not load history/),
    ).not.toBeInTheDocument();
    expect(mockActivate).not.toHaveBeenCalled();
  });

  it("retains stale versions after a refresh failure and retries only the read", async () => {
    const refetch = vi.fn();
    const onClose = vi.fn();
    const data = [
      { id: "cfg-7", version: 7, is_active: true },
      { id: "cfg-6", version: 6 },
    ];
    mockHistoryReturn = { data, isSuccess: true, refetch };
    const { rerender } = renderDrawer(onClose);
    mockHistoryReturn = {
      data,
      isError: true,
      error: { statusCode: 500 },
      refetch,
    };
    rerender(
      <QueryClientProvider client={client}>
        <ConfigHistoryDrawer open onClose={onClose} />
      </QueryClientProvider>,
    );
    expect(screen.getByText(/stale/i)).toBeInTheDocument();
    expect(screen.getByText("v7")).toBeInTheDocument();
    expect(screen.getByText("v6")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(mockActivate).not.toHaveBeenCalled();
    expect(get).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "Config History" }),
    ).toBeInTheDocument();
  });

  it("does not show empty history while the first read is pending", () => {
    mockHistoryReturn = {
      data: undefined,
      isPending: true,
      isFetching: true,
    };
    renderDrawer();
    expect(screen.getByText(/Loading history/)).toBeInTheDocument();
    expect(screen.queryByText(/No config history/)).not.toBeInTheDocument();
  });

  it("clamps history to the viewport and keeps a 480px desktop width", () => {
    renderDrawer();
    const paper = screen
      .getByText("Config History")
      .closest(".MuiDrawer-paper");
    expect(paper).toHaveStyle({ maxWidth: "100%" });

    // jsdom does not apply media queries or measure layout. Inspect the
    // generated phone/desktop CSS instead; real 390px layout is owed to E2E.
    const widths = Array.from(document.styleSheets)
      .flatMap((sheet) => Array.from(sheet.cssRules))
      .filter((rule) => rule.media)
      .flatMap((rule) =>
        Array.from(rule.cssRules)
          .filter(
            (child) =>
              child.selectorText && paper.matches(child.selectorText),
          )
          .map((child) => ({
            media: rule.media.mediaText,
            width: child.style.width,
          })),
      );
    expect(widths).toEqual(
      expect.arrayContaining([
        { media: "(min-width:0px)", width: "100%" },
        { media: "(min-width:600px)", width: "480px" },
      ]),
    );
  });

  it("exposes the shared drawer's named dialog and close control", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderDrawer(onClose);
    expect(
      screen.getByRole("dialog", { name: "Config History" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
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

  it("masks stored secrets when a version is viewed", async () => {
    const user = userEvent.setup();
    mockHistoryReturn = {
      data: [
        {
          id: "cfg-9",
          version: 9,
          is_active: true,
          alerting: {
            channels: [
              {
                name: "ops",
                type: "slack",
                url: "https://hooks.slack.com/services/T0/B0/SECRETX",
              },
            ],
          },
          cache: { redis: { address: "redis:6379", password: "SECRETY" } },
        },
      ],
      isLoading: false,
    };
    renderDrawer();
    await user.click(screen.getByRole("button", { name: /view/i }));
    const dialog = await screen.findByRole("dialog", { name: /Config v9/ });
    expect(dialog.textContent).not.toMatch(/SECRET[XY]/);
    expect(dialog.textContent).toContain("hooks.slack.com");
    expect(dialog.textContent).toContain("redis:6379");
  });

  it("R3-CONTROLS-08 inspects and copies all returned sections with masking", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    mockHistoryReturn = { data: [{
      id: "cfg-12", version: 12, is_active: true,
      rate_limiting: { rpm: 42 }, cost_tracking: { enabled: true },
      model_database: { custom: { input_cost: 0.01 } },
      model_map: { fast: "provider-7" },
      extra_section: { api_key: "fixture-hidden-value", limit: 3 },
    }], isLoading: false };
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "View" }));
    const dialog = within(screen.getByRole("dialog", { name: "Config v12" }));
    const shown = dialog.getByTestId("history-config-json");
    for (const section of ["rate_limiting", "cost_tracking", "model_database", "model_map", "extra_section"]) {
      expect(shown).toHaveTextContent(section);
    }
    expect(shown).not.toHaveTextContent("fixture-hidden-value");
    await user.click(dialog.getByRole("button", { name: "Copy shown JSON" }));
    expect(JSON.parse(writeText.mock.calls[0][0]).extra_section.api_key).toBe("••••••••");
    await user.click(dialog.getByLabelText("Section or field"));
    await user.click(screen.getByRole("option", { name: "model_map" }));
    expect(JSON.parse(shown.textContent)).toEqual({ model_map: { fast: "provider-7" } });
    await user.type(dialog.getByLabelText("Search returned configuration"), "does-not-exist");
    expect(dialog.getByText("No matching fields in this view.")).toBeInTheDocument();
    expect(mockActivate).not.toHaveBeenCalled();
    expect(get).not.toHaveBeenCalled();
    writeText.mockRestore();
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
        await screen.findByText(/turns Future AGI credits off/i),
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
      await screen.findByText(/turns Future AGI credits off/i);
      await user.click(screen.getByRole("button", { name: "Cancel" }));

      await waitFor(() =>
        expect(
          screen.queryByText(/turns Future AGI credits off/i),
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
          warnings: [
            { code: "managed_models_change", from: true, to: false },
          ],
        }),
      );
      renderDrawer();

      await user.click(screen.getByRole("button", { name: "Activate" }));
      await screen.findByText(/turns Future AGI credits off/i);
      await user.click(screen.getByRole("button", { name: "Activate v2" }));

      expect(snackbar).toHaveBeenCalledWith(
        expect.stringMatching(/Future AGI credits are now off/i),
        expect.objectContaining({ variant: "warning" }),
      );
      const keys = invalidate.mock.calls.map(([arg]) => arg.queryKey[0]);
      expect(keys).toContain("agentcc-gateway-config");
      await waitFor(() =>
        expect(
          screen.queryByText(/turns Future AGI credits off/i),
        ).not.toBeInTheDocument(),
      );
    });
  });
});
