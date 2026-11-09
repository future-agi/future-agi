import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HelmetProvider } from "react-helmet-async";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import FallbacksSection from "./FallbacksSection";

const h = vi.hoisted(() => ({ canEdit: true, save: vi.fn() }));
const routing = {
  fallback_enabled: true,
  model_fallbacks: { primary: ["secondary"] },
  failover: { enabled: true, on_status_codes: [429] },
  model_timeouts: { primary: "30s" },
};
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({
    canEdit: h.canEdit,
    disabledReason: h.canEdit ? null : "Owner or Admin only",
  }),
}));
vi.mock("./hooks/useFallbackConfig", () => ({
  useFallbackConfig: () => ({
    routing,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    saveRouting: h.save,
    isSaving: false,
  }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({
    data: {
      providers: [{ name: "fixture", models: ["primary", "secondary"] }],
    },
  }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "fixture",
    gateway: { id: "fixture" },
  }),
}));
beforeEach(() => {
  h.canEdit = true;
  h.save.mockReset();
});
const show = () =>
  render(
    <HelmetProvider>
      <FallbacksSection />
    </HelmetProvider>,
  );

/** @type {Array<[string, string, (user: ReturnType<typeof userEvent.setup>) => Promise<void>]>} */
const cases = [
  [
    "chain",
    "Fallback chain primary",
    async (user) =>
      user.click(screen.getByRole("button", { name: "Remove this chain" })),
  ],
  [
    "fallback",
    "Fallback secondary from primary",
    async (user) =>
      user.click(screen.getByRole("button", { name: "Remove fallback 1" })),
  ],
  [
    "status code",
    "Status code 429",
    async (user) => {
      await user.click(
        screen.getByRole("button", { name: "Provider failover" }),
      );
      const chip = screen.getByRole("button", { name: "429" });
      chip.focus();
      await user.keyboard("{Delete}");
    },
  ],
  [
    "timeout",
    "Timeout for primary",
    async (user) => {
      await user.click(screen.getByRole("button", { name: "Model timeouts" }));
      const chip = screen.getByRole("button", { name: "primary: 30s" });
      chip.focus();
      await user.keyboard("{Delete}");
    },
  ],
];

describe("release fallback draft removals", () => {
  it.each(cases)(
    "confirms the named %s before changing a draft, and keeps Cancel inert",
    async (_kind, name, remove) => {
      const user = userEvent.setup();
      show();
      await remove(user);
      const dialog = await screen.findByRole("dialog", {
        name: "Remove configuration",
      });
      expect(dialog).toHaveTextContent(`Remove "${name}" from the draft?`);
      expect(dialog).toHaveTextContent("Save & Apply");
      expect(h.save).not.toHaveBeenCalled();
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(dialog).not.toBeInTheDocument());
      expect(
        screen.queryByRole("button", { name: /Save & apply/i }),
      ).toBeNull();
      await remove(user);
      const confirmation = await screen.findByRole("dialog", {
        name: "Remove configuration",
      });
      await user.click(
        within(confirmation).getByRole("button", { name: "Remove" }),
      );
      await waitFor(() => expect(confirmation).not.toBeInTheDocument());
      expect(
        screen.getAllByRole("button", { name: /Save & apply/i }).length,
      ).toBeGreaterThan(0);
      expect(h.save).not.toHaveBeenCalled();
      await user.click(
        screen.getAllByRole("button", { name: /Save & apply/i })[0],
      );
      expect(h.save).toHaveBeenCalledTimes(1);
      expect(h.save).toHaveBeenCalledWith({
        ...routing,
        ...(_kind === "chain" ? { model_fallbacks: {} } : {}),
        ...(_kind === "fallback" ? { model_fallbacks: { primary: [] } } : {}),
        ...(_kind === "status code"
          ? { failover: { ...routing.failover, on_status_codes: [] } }
          : {}),
        ...(_kind === "timeout" ? { model_timeouts: {} } : {}),
      });
    },
  );
});
