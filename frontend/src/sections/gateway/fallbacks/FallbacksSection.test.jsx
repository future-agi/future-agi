import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "../../../utils/test-utils";
import userEvent from "@testing-library/user-event";
import { createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { customShadows } from "src/theme/custom-shadows";
import { componentsOverrides } from "src/theme/overrides";
import FallbacksSection from "./FallbacksSection";

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
        screen.getByText(/Failed to load config: Network error/),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /try again/i }),
      ).toBeInTheDocument();
    });

    it("calls refetch when Try Again is clicked", async () => {
      hookReturn = {
        ...hookReturn,
        routing: {},
        error: { message: "Oops" },
      };
      render(<FallbacksSection />);
      await userEvent.click(screen.getByRole("button", { name: /try again/i }));
      expect(mockRefetch).toHaveBeenCalledTimes(1);
    });
  });

  // ---- Loaded state — header ----
  describe("header", () => {
    it("renders the section header", () => {
      render(<FallbacksSection />);
      expect(screen.getByText("Fallbacks & Reliability")).toBeInTheDocument();
      expect(
        screen.getByText(
          /Configure model fallback chains and provider reliability/,
        ),
      ).toBeInTheDocument();
    });
  });

  // ---- Model Fallback Chains ----
  describe("Model Fallback Chains", () => {
    it("renders existing fallback chains", () => {
      render(<FallbacksSection />);
      expect(screen.getByText("Model Fallback Chains")).toBeInTheDocument();
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
        screen.getByRole("button", { name: /Add Fallback Chain/i }),
      ).toBeInTheDocument();
    });

    it("adds a new chain when Add Fallback Chain is clicked", async () => {
      render(<FallbacksSection />);
      await userEvent.click(
        screen.getByRole("button", { name: /Add Fallback Chain/i }),
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
      // Now only 1 chain
      await waitFor(() => {
        expect(screen.getAllByText("Primary")).toHaveLength(1);
      });
    });
  });

  // ---- Provider Failover ----
  describe("Provider Failover", () => {
    it("renders failover section", () => {
      render(<FallbacksSection />);
      expect(screen.getByText("Provider Failover")).toBeInTheDocument();
    });

    it("shows status code chips", async () => {
      render(<FallbacksSection />);
      // Expand the Provider Failover section
      await userEvent.click(screen.getByText("Provider Failover"));
      // Multiple sections may share codes, so use getAllByText
      expect(screen.getAllByText("429").length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText("500").length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText("502").length).toBeGreaterThanOrEqual(1);
    });
  });

  // ---- Retry ----
  describe("Retry", () => {
    it("renders retry section", () => {
      render(<FallbacksSection />);
      expect(screen.getByText("Retry")).toBeInTheDocument();
    });
  });

  // ---- Circuit Breaker ----
  describe("Circuit Breaker", () => {
    it("renders circuit breaker section", () => {
      render(<FallbacksSection />);
      expect(screen.getByText("Circuit Breaker")).toBeInTheDocument();
    });
  });

  // ---- Model Timeouts ----
  describe("Model Timeouts", () => {
    it("renders model timeouts section", () => {
      render(<FallbacksSection />);
      expect(screen.getByText("Model Timeouts")).toBeInTheDocument();
    });

    it("shows existing timeout entries when section is expanded", async () => {
      render(<FallbacksSection />);
      await userEvent.click(screen.getByText("Model Timeouts"));
      expect(screen.getByText("o1: 300s")).toBeInTheDocument();
      expect(screen.getByText("gpt-4o: 60s")).toBeInTheDocument();
    });
  });

  // ---- Save & Discard ----
  describe("save and discard", () => {
    describe.each(["light", "dark"])("%s production theme", (mode) => {
      const theme = createTheme({
        palette: palette(mode),
        customShadows: customShadows(mode),
      });
      theme.components = componentsOverrides(theme);

      it.each([
        ["inline banner", 0],
        ["sticky bar", 1],
      ])(
        "uses primary colors for the %s save button",
        async (_label, index) => {
          render(<FallbacksSection />, { theme });
          await userEvent.click(
            screen.getByRole("button", { name: /Add Fallback Chain/i }),
          );

          const saveButtons = screen.getAllByRole("button", {
            name: /Save & Apply/i,
          });
          expect(saveButtons).toHaveLength(2);
          expect(saveButtons[index]).toHaveStyle({
            "background-color": theme.palette.primary.main,
            color: theme.palette.primary.contrastText,
          });
        },
      );
    });

    it("does not show save bar initially (no changes)", () => {
      render(<FallbacksSection />);
      expect(screen.queryByText("Save & Apply")).not.toBeInTheDocument();
    });

    it("shows save bar after making a change", async () => {
      render(<FallbacksSection />);
      // Add a chain to trigger a change
      await userEvent.click(
        screen.getByRole("button", { name: /Add Fallback Chain/i }),
      );
      // Both the alert and sticky bar show unsaved messages
      expect(
        screen.getAllByText(/unsaved changes/i).length,
      ).toBeGreaterThanOrEqual(1);
      expect(
        screen.getAllByRole("button", { name: /Save & Apply/i }),
      ).toHaveLength(2); // top alert + sticky bar
    });

    it("calls saveRouting with draft when Save is clicked", async () => {
      mockSaveRouting.mockResolvedValue({});
      render(<FallbacksSection />);

      // Make a change
      await userEvent.click(
        screen.getByRole("button", { name: /Add Fallback Chain/i }),
      );

      // Click the first "Save & Apply" button
      const saveButtons = screen.getAllByRole("button", {
        name: /Save & Apply/i,
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
        screen.getByRole("button", { name: /Add Fallback Chain/i }),
      );
      const saveButtons = screen.getAllByRole("button", {
        name: /Save & Apply/i,
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
        screen.getByRole("button", { name: /Add Fallback Chain/i }),
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
        screen.getByRole("button", { name: /Add Fallback Chain/i }),
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
      expect(screen.getByText("Fallbacks & Reliability")).toBeInTheDocument();
      expect(screen.getAllByText("Primary")).toHaveLength(1);
    });
  });
});
