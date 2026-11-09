import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "../../../utils/test-utils";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import FallbacksSectionPage from "./FallbacksSection";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

// The gateway admin role check (useGatewayAdmin): saving is Owner or Admin
// only. The cases below run as an Admin unless one says otherwise.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

const FallbacksSection = () => (
  <HelmetProvider>
    <FallbacksSectionPage />
  </HelmetProvider>
);

// ---------------------------------------------------------------------------
// Mock the useFallbackConfig hook
// ---------------------------------------------------------------------------

const mockSaveRouting = vi.fn();
const mockRefetch = vi.fn();

const DEFAULT_ROUTING = {
  fallback_enabled: true,
  default_model: "gpt-4o",
  model_fallbacks: {
    "gpt-4o": ["claude-sonnet-4-20250514", "gpt-4-turbo"],
    "claude-sonnet-4-20250514": ["gpt-4o"],
  },
  failover: {
    enabled: true,
    max_attempts: 3,
    on_status_codes: [429, 500, 502, 503, 504],
    on_timeout: true,
    per_attempt_timeout: "30s",
  },
  retry: {
    enabled: false,
    max_retries: 2,
    initial_delay: "500ms",
    max_delay: "10s",
    multiplier: 2.0,
    on_status_codes: [429, 500, 502, 503],
    on_timeout: true,
  },
  circuit_breaker: {
    enabled: true,
    failure_threshold: 5,
    success_threshold: 2,
    cooldown: "30s",
    on_status_codes: [500, 502, 503, 504],
  },
  model_timeouts: {
    o1: "300s",
    "gpt-4o": "60s",
  },
};

let hookReturn;

vi.mock("./hooks/useFallbackConfig", () => ({
  useFallbackConfig: () => hookReturn,
}));

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "test-gateway-id",
    gateway: { id: "test-gateway-id" },
  }),
}));

const mockProviderHealth = {
  providers: [
    {
      name: "openai",
      status: "healthy",
      models: Object.keys(DEFAULT_ROUTING.model_fallbacks).concat(
        ...Object.values(DEFAULT_ROUTING.model_fallbacks),
      ),
    },
  ],
};

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({ data: mockProviderHealth }),
}));

beforeEach(() => {
  mockOrgLevel = 8;
  mockSaveRouting.mockReset();
  mockRefetch.mockReset();
  hookReturn = {
    routing: { ...DEFAULT_ROUTING },
    isLoading: false,
    error: null,
    refetch: mockRefetch,
    saveRouting: mockSaveRouting,
    isSaving: false,
  };
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("FallbacksSection", () => {
  describe("Owner or Admin only", () => {
    // Fallbacks save the org's gateway config, as the Org config editor
    // does: only an Owner or Admin changes them. Anyone else reads every
    // chain and switch, with each control disabled and naming the role.
    it.each(NOT_ADMIN_LEVELS)(
      "%s sees every switch, Add fallback chain and each chain's controls disabled, with the role they need",
      (_who, level) => {
        mockOrgLevel = level;
        render(<FallbacksSection />);
        // Reads are unchanged.
        expect(screen.getAllByText("Primary")).toHaveLength(2);
        expectAdminOnly(
          screen.getByRole("button", { name: /Add fallback chain/i }),
        );
        for (const name of [
          "Model fallback chains on",
          "Provider failover on",
          "Retry on",
          "Circuit breaker on",
        ]) {
          expectAdminOnly(screen.getByRole("checkbox", { name }));
        }
        screen
          .getAllByRole("button", { name: "Remove this chain" })
          .forEach(expectAdminOnly);
        screen
          .getAllByRole("button", { name: /^Add fallback$/ })
          .forEach(expectAdminOnly);
        for (const select of screen.getAllByRole("combobox", {
          name: "Primary model",
        })) {
          expect(select).toHaveAttribute("aria-disabled", "true");
        }
        // Nothing can be changed, so there is nothing to save.
        expect(
          screen.queryByRole("button", { name: /Save & apply/i }),
        ).toBeNull();
        expect(mockSaveRouting).not.toHaveBeenCalled();
      },
    );

    it.each(NOT_ADMIN_LEVELS)(
      "%s sees the required role on timeout switches",
      async (_who, level) => {
        mockOrgLevel = level;
        render(<FallbacksSection />);
        await userEvent.click(
          screen.getByRole("button", { name: "Provider failover" }),
        );
        await userEvent.click(screen.getByRole("button", { name: "Retry" }));
        expectAdminOnly(
          screen.getByRole("checkbox", { name: "Failover on timeout" }),
        );
        expectAdminOnly(
          screen.getByRole("checkbox", { name: "Retry on timeout" }),
        );
      },
    );

    it.each(ADMIN_LEVELS)("%s can save", async (_who, level) => {
      mockOrgLevel = level;
      render(<FallbacksSection />);
      await userEvent.click(
        screen.getByRole("button", { name: /Add fallback chain/i }),
      );
      screen
        .getAllByRole("button", { name: /Save & apply/i })
        .forEach(expectEditable);
    });
  });

  // ---- Loading state ----
  describe("loading state", () => {
    it("shows skeleton loaders while loading", () => {
      hookReturn = {
        ...hookReturn,
        routing: {},
        isLoading: true,
      };
      const { container } = render(<FallbacksSection />);
      const skeletons = container.querySelectorAll(".MuiSkeleton-root");
      expect(skeletons.length).toBeGreaterThanOrEqual(4);
    });
  });

  // ---- Error state ----
  describe("error state", () => {
    it("shows error alert with retry button", () => {
      hookReturn = {
        ...hookReturn,
        routing: {},
        error: { message: "Network error" },
      };
      render(<FallbacksSection />);
      expect(
        screen.getByText("Couldn't load the fallback config"),
      ).toBeInTheDocument();
      expect(screen.getByText("Network error")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /retry/i }),
      ).toBeInTheDocument();
    });

    it("calls refetch when Retry is clicked", async () => {
      hookReturn = {
        ...hookReturn,
        routing: {},
        error: { message: "Oops" },
      };
      render(<FallbacksSection />);
      await userEvent.click(screen.getByRole("button", { name: /retry/i }));
      expect(mockRefetch).toHaveBeenCalledTimes(1);
    });
  });

  // ---- Loaded state — header ----
  describe("header", () => {
    it("renders the section header", () => {
      render(<FallbacksSection />);
      expect(
        screen.getByRole("heading", { level: 1, name: "Fallbacks" }),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          /What the gateway tries when a model or provider fails/,
        ),
      ).toBeInTheDocument();
    });
  });

  // ---- Model Fallback Chains ----
  describe("Model fallback chains", () => {
    // The card opened on an empty "Default model" select
    // with a not-enforced chip. Its first control after the on/off switch is
    // now the first chain's primary model, a setting that is applied.
    it("leads with its first real control: the first chain's primary model", () => {
      render(<FallbacksSection />);
      const chains = screen.getByTestId("fallbacks-section-chains");
      const [firstField] = within(chains).getAllByRole("combobox");
      expect(firstField).toHaveAccessibleName("Primary model");
      expect(within(chains).queryByLabelText("Default model")).toBeNull();
      expect(
        within(chains).queryByText("Not enforced on this gateway"),
      ).toBeNull();
    });

    it("renders existing fallback chains", () => {
      render(<FallbacksSection />);
      expect(screen.getByText("Model fallback chains")).toBeInTheDocument();
      // Should have 2 chains from DEFAULT_ROUTING
      const primaryChips = screen.getAllByText("Primary");
      expect(primaryChips).toHaveLength(2);
    });

    it("shows empty state when no chains configured", () => {
      hookReturn.routing = { ...DEFAULT_ROUTING, model_fallbacks: {} };
      render(<FallbacksSection />);
      expect(
        screen.getByText(/No fallback chains configured/),
      ).toBeInTheDocument();
    });

    it("shows Add Fallback Chain button", () => {
      render(<FallbacksSection />);
      expect(
        screen.getByRole("button", { name: /Add fallback chain/ }),
      ).toBeInTheDocument();
    });

    it("adds a new chain when Add Fallback Chain is clicked", async () => {
      render(<FallbacksSection />);
      await userEvent.click(
        screen.getByRole("button", { name: /Add fallback chain/ }),
      );
      // Should now have 3 Primary chips (was 2)
      const primaryChips = screen.getAllByText("Primary");
      expect(primaryChips).toHaveLength(3);
    });

    it("removes a chain when delete is clicked", async () => {
      render(<FallbacksSection />);
      // Initially 2 chains
      expect(screen.getAllByText("Primary")).toHaveLength(2);

      // Find delete buttons by aria-label from Tooltip
      const deleteButtons = screen.getAllByLabelText("Remove this chain");
      expect(deleteButtons).toHaveLength(2);

      await userEvent.click(deleteButtons[0]);
      const confirmation = await screen.findByRole("dialog", {
        name: "Remove configuration",
      });
      expect(confirmation).toHaveTextContent("Fallback chain gpt-4o");
      expect(screen.getAllByText("Primary")).toHaveLength(2);
      await userEvent.click(
        within(confirmation).getByRole("button", { name: "Remove" }),
      );
      // Now only 1 chain
      await waitFor(() => {
        expect(screen.getAllByText("Primary")).toHaveLength(1);
      });
    });
  });

  // ---- Sentence case, as on the other rebuilt gateway pages ----
  describe("labels", () => {
    it("are in sentence case, as on the other rebuilt gateway pages", async () => {
      render(<FallbacksSection />);
      await userEvent.click(screen.getByText("Model timeouts"));
      [
        "Model fallback chains",
        "Provider failover",
        "Retry",
        "Circuit breaker",
        "Model timeouts",
      ].forEach((text) =>
        expect(screen.getAllByText(text).length, text).toBeGreaterThan(0),
      );
      expect(
        screen.getByRole("button", { name: "Add fallback chain" }),
      ).toBeInTheDocument();
      expect(
        screen.getAllByRole("button", { name: "Add fallback" }).length,
      ).toBeGreaterThan(0);
    });
  });

  // ---- The reliability settings run for the org's keys ----
  // The gateway runs the org's provider failover, retry and circuit breaker
  // for its keys, so each is a section with its own switch and fields; the
  // Default Model control is not offered.
  describe("reliability settings", () => {
    it("edits provider failover, retry and the circuit breaker", async () => {
      mockSaveRouting.mockResolvedValue({});
      render(<FallbacksSection />);
      await userEvent.click(
        screen.getByRole("button", { name: "Provider failover" }),
      );
      const failover = screen.getByTestId("fallbacks-section-failover");
      expect(within(failover).getByLabelText("Max attempts")).toHaveValue(3);
      const onTimeout = within(failover).getByRole("checkbox", {
        name: "Failover on timeout",
      });
      expect(onTimeout).toBeChecked();
      await userEvent.click(onTimeout);
      await userEvent.click(
        screen.getByRole("button", { name: /Save & apply/ }),
      );
      expect(mockSaveRouting.mock.calls[0][0].failover).toMatchObject({
        enabled: true,
        max_attempts: 3,
        on_status_codes: [429, 500, 502, 503, 504],
        on_timeout: false,
      });
    });

    it("reads each switch as the gateway runs it", () => {
      render(<FallbacksSection />);
      const sw = (name) => screen.getByRole("checkbox", { name });
      expect(sw("Model fallback chains on")).toBeChecked();
      expect(sw("Provider failover on")).toBeChecked();
      expect(sw("Retry on")).not.toBeChecked();
      expect(sw("Circuit breaker on")).toBeChecked();
    });

    it("does not offer the Default model control and keeps a stored one", async () => {
      mockSaveRouting.mockResolvedValue({});
      render(<FallbacksSection />);
      expect(screen.queryByText(/Default model/i)).toBeNull();
      await userEvent.click(
        screen.getByRole("button", { name: /Add fallback chain/i }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: /Save & apply/ }),
      );
      expect(mockSaveRouting.mock.calls[0][0].default_model).toBe("gpt-4o");
    });

    it("says a turned-off switch leaves its chains saved but not tried", async () => {
      render(<FallbacksSection />);
      await userEvent.click(
        screen.getByRole("checkbox", { name: "Model fallback chains on" }),
      );
      expect(
        screen.getByText(/saved but not tried: fallbacks are off/i),
      ).toBeInTheDocument();
    });
  });

  // ---- Model Timeouts ----
  describe("Model timeouts", () => {
    it("renders model timeouts section", () => {
      render(<FallbacksSection />);
      expect(screen.getByText("Model timeouts")).toBeInTheDocument();
    });

    it("shows existing timeout entries when section is expanded", async () => {
      render(<FallbacksSection />);
      await userEvent.click(screen.getByText("Model timeouts"));
      expect(screen.getByText("o1: 300s")).toBeInTheDocument();
      expect(screen.getByText("gpt-4o: 60s")).toBeInTheDocument();
    });
  });

  // ---- Save & Discard ----
  describe("save and discard", () => {
    it("does not show save bar initially (no changes)", () => {
      render(<FallbacksSection />);
      expect(screen.queryByText("Save & apply")).not.toBeInTheDocument();
    });

    it("shows save bar after making a change", async () => {
      render(<FallbacksSection />);
      // Add a chain to trigger a change
      await userEvent.click(
        screen.getByRole("button", { name: /Add fallback chain/ }),
      );
      // One save place: the sticky bar.
      expect(
        screen.getAllByText(/unsaved changes/i).length,
      ).toBeGreaterThanOrEqual(1);
      expect(
        screen.getAllByRole("button", { name: /Save & apply/ }),
      ).toHaveLength(1);
    });

    it("calls saveRouting with draft when Save is clicked", async () => {
      mockSaveRouting.mockResolvedValue({});
      render(<FallbacksSection />);

      // Make a change
      await userEvent.click(
        screen.getByRole("button", { name: /Add fallback chain/ }),
      );

      // Click the first "Save & apply" button
      const saveButtons = screen.getAllByRole("button", {
        name: /Save & apply/,
      });
      await userEvent.click(saveButtons[0]);

      expect(mockSaveRouting).toHaveBeenCalledTimes(1);
      // The argument should be the updated routing object with the new chain
      const savedRouting = mockSaveRouting.mock.calls[0][0];
      expect(savedRouting.model_fallbacks).toBeDefined();
      expect(
        Object.keys(savedRouting.model_fallbacks).length,
      ).toBeGreaterThanOrEqual(3);
    });

    it("hides save bar after successful save", async () => {
      mockSaveRouting.mockResolvedValue({});
      render(<FallbacksSection />);

      await userEvent.click(
        screen.getByRole("button", { name: /Add fallback chain/ }),
      );
      const saveButtons = screen.getAllByRole("button", {
        name: /Save & apply/,
      });
      await userEvent.click(saveButtons[0]);

      await waitFor(() => {
        expect(screen.queryByText(/unsaved changes/i)).not.toBeInTheDocument();
      });
    });

    it("shows Discard button in header when changes exist", async () => {
      render(<FallbacksSection />);
      expect(
        screen.queryByRole("button", { name: /Discard/i }),
      ).not.toBeInTheDocument();

      await userEvent.click(
        screen.getByRole("button", { name: /Add fallback chain/ }),
      );
      expect(
        screen.getAllByRole("button", { name: /Discard/i }).length,
      ).toBeGreaterThanOrEqual(1);
    });

    it("reverts draft on Discard", async () => {
      render(<FallbacksSection />);
      // Initially 2 chains
      expect(screen.getAllByText("Primary")).toHaveLength(2);

      // Add a chain → 3 chains
      await userEvent.click(
        screen.getByRole("button", { name: /Add fallback chain/ }),
      );
      expect(screen.getAllByText("Primary")).toHaveLength(3);

      // Discard → back to 2
      const discardButtons = screen.getAllByRole("button", {
        name: /Discard/i,
      });
      await userEvent.click(discardButtons[0]);

      await waitFor(() => {
        expect(screen.getAllByText("Primary")).toHaveLength(2);
      });
    });
  });

  // ---- Accessibility and narrow screens ----
  describe("accessibility", () => {
    it("every combobox and icon button has an accessible name; the header toggle contains no switch", async () => {
      const { container } = render(<FallbacksSection />);
      // Open every section so all of its controls render.
      for (const title of [
        "Provider failover",
        "Retry",
        "Circuit breaker",
        "Model timeouts",
      ]) {
        await userEvent.click(screen.getByText(title));
      }
      const combos = screen.getAllByRole("combobox");
      expect(combos.length).toBeGreaterThan(0);
      combos.forEach((combo) => expect(combo).toHaveAccessibleName());
      expect(
        screen.getAllByRole("combobox", { name: "Primary model" }),
      ).toHaveLength(2);
      expect(
        screen.getByRole("combobox", { name: "Fallback 2" }),
      ).toBeInTheDocument();

      const iconButtons = container.querySelectorAll(".MuiIconButton-root");
      expect(iconButtons.length).toBeGreaterThan(0);
      iconButtons.forEach((button) => expect(button).toHaveAccessibleName());
      expect(
        screen.getAllByRole("button", { name: "Remove fallback 1" }),
      ).toHaveLength(2);

      // The expand toggle is its own button; the on/off switch sits beside
      // it, never inside it (axe nested-interactive).
      const toggles = container.querySelectorAll("[aria-expanded]");
      expect(toggles.length).toBeGreaterThan(0);
      toggles.forEach((toggle) => {
        expect(toggle.querySelector("input")).toBeNull();
        expect(toggle.getAttribute("role")).not.toBe("button");
      });
      container
        .querySelectorAll("section > div:first-of-type")
        .forEach((header) => expect(header).not.toHaveAttribute("role"));
    });

    it("keeps each fallback step (arrow, #n, model, remove) on one line", () => {
      render(<FallbacksSection />);
      const steps = screen.getAllByTestId("fallback-step");
      // gpt-4o has two fallbacks, claude-sonnet-4 one.
      expect(steps).toHaveLength(3);
      steps.forEach((step, i) => {
        expect(step).toHaveStyle({ flexWrap: "nowrap" });
        expect(within(step).getByRole("combobox")).toBeInTheDocument();
        expect(
          within(step).getByRole("button", { name: /^Remove fallback/ }),
        ).toBeInTheDocument();
        // Seen at 390 px: the #2 chip was squeezed to "#...". The arrow, the
        // number and the x keep their size; the model select gives way.
        expect(
          within(step).getByText(/^#\d$/).closest(".MuiChip-root"),
        ).toHaveStyle({ flexShrink: "0" });
        expect(
          within(step).getByRole("button", { name: /^Remove fallback/ }),
        ).toHaveStyle({ flexShrink: "0" });
        expect(i).toBeGreaterThanOrEqual(0);
      });
    });
  });

  // ---- camelCase compatibility ----
  describe("camelCase field compatibility", () => {
    it("handles camelCase field names from API", () => {
      hookReturn.routing = {
        fallbackEnabled: true,
        defaultModel: "gpt-4o-mini",
        modelFallbacks: { "gpt-4o-mini": ["gpt-3.5-turbo"] },
        failover: { enabled: true, maxAttempts: 5 },
        retry: { enabled: false },
        circuitBreaker: { enabled: false },
        modelTimeouts: { o1: "120s" },
      };
      render(<FallbacksSection />);
      // Should render without crashing
      expect(
        screen.getByRole("heading", { level: 1, name: "Fallbacks" }),
      ).toBeInTheDocument();
      expect(screen.getAllByText("Primary")).toHaveLength(1);
    });
  });
});
