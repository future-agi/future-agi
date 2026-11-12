import { beforeEach, describe, it, expect, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import FallbacksSection from "./FallbacksSection";
import RoutingConfigTab from "../settings/RoutingConfigTab";
import { FALLBACK_LIMIT_MESSAGE } from "./fallbackLimits";

const { permission } = vi.hoisted(() => ({ permission: { orgLevel: 8 } }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => permission,
}));
beforeEach(() => {
  permission.orgLevel = 8;
});
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["m1", "m2"],
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw" }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({
    data: { providers: [{ name: "example", models: ["m1", "m2"] }] },
  }),
}));

let routing;
const saveRouting = vi.fn();
vi.mock("./hooks/useFallbackConfig", () => ({
  useFallbackConfig: () => ({
    routing,
    isLoading: false,
    error: null,
    saveRouting,
  }),
}));

// FallbacksSection renders GatewayPageFrame, whose <Helmet> needs the
// react-helmet-async HelmetProvider; test-utils' render does not add one.
const renderFallbacksSection = () =>
  render(
    <HelmetProvider>
      <FallbacksSection />
    </HelmetProvider>,
  );

const chain = (length, spelling) => ({
  [spelling]: { m1: Array(length).fill("m2") },
});

describe.each(["model_fallbacks", "modelFallbacks"])(
  "Fallback limit (%s)",
  (spelling) => {
    it("lets the Fallbacks page add the fifteenth but not a sixteenth, and remove one", async () => {
      routing = chain(14, spelling);
      renderFallbacksSection();
      const add = screen.getByRole("button", { name: "Add fallback" });
      expect(add).toBeEnabled();
      await userEvent.click(add);
      expect(add).toBeDisabled();
      expect(screen.getByRole("status")).toHaveTextContent(
        "Up to 15 fallback models per model",
      );
      // A native click on a disabled button is ignored; userEvent correctly
      // refuses pointer interaction with MUI's pointer-events: none style.
      add.click();
      expect(
        screen.getAllByRole("button", { name: /^Remove fallback \d+$/ }),
      ).toHaveLength(15);
      await userEvent.click(
        screen.getByRole("button", { name: "Remove fallback 15" }),
      );
      expect(add).toBeDisabled();
      expect(screen.getByRole("dialog")).toHaveTextContent(
        "Click Save & apply to apply the removal.",
      );
      await userEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "Remove",
        }),
      );
      expect(add).toBeEnabled();
      expect(
        screen.queryByText(FALLBACK_LIMIT_MESSAGE),
      ).not.toBeInTheDocument();
    });

    it("bounds the Routing settings editor without blocking removal", async () => {
      const onChange = vi.fn();
      const view = render(
        <RoutingConfigTab routing={chain(14, spelling)} onChange={onChange} />,
      );
      const add = screen.getByRole("button", { name: "Add" });
      await userEvent.click(add);
      const updated = onChange.mock.calls[0][0];
      expect(updated.model_fallbacks.m1).toHaveLength(15);
      view.rerender(<RoutingConfigTab routing={updated} onChange={onChange} />);
      expect(add).toBeDisabled();
      expect(screen.getByRole("status")).toHaveTextContent(
        FALLBACK_LIMIT_MESSAGE,
      );
      onChange.mockClear();
      add.click();
      expect(onChange).not.toHaveBeenCalled();
      await userEvent.click(
        screen.getByRole("button", { name: "Remove fallback 15" }),
      );
      expect(onChange).not.toHaveBeenCalled();
      expect(screen.getByRole("dialog")).toHaveTextContent(
        "Click Save & Activate to apply the removal.",
      );
      await userEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "Remove",
        }),
      );
      expect(onChange.mock.calls[0][0].model_fallbacks.m1).toHaveLength(14);
    });

    it("warns about a legacy chain and trims it to 15 before saving", async () => {
      routing = chain(20, spelling);
      saveRouting.mockClear();
      renderFallbacksSection();
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Only the first 15 fallback models are used",
      );
      await userEvent.click(screen.getByRole("button", { name: "Trim to 15" }));
      expect(screen.queryByText(/Only the first 15/)).not.toBeInTheDocument();
      expect(
        screen.getAllByRole("button", { name: /^Remove fallback \d+$/ }),
      ).toHaveLength(15);
      await userEvent.click(
        screen.getAllByRole("button", { name: "Save & apply" })[0],
      );
      expect(saveRouting).toHaveBeenCalledWith(
        expect.objectContaining({
          model_fallbacks: { m1: Array(15).fill("m2") },
        }),
      );
    });

    it("trims a legacy Routing settings chain in order without touching other routing", async () => {
      const onChange = vi.fn();
      const config = { ...chain(20, spelling), strategy: "round_robin" };
      const view = render(
        <RoutingConfigTab routing={config} onChange={onChange} />,
      );
      expect(screen.getByText(/Only the first 15/)).toBeVisible();
      await userEvent.click(screen.getByRole("button", { name: "Trim to 15" }));
      const updated = onChange.mock.calls[0][0];
      expect(updated.model_fallbacks.m1).toEqual(
        config[spelling].m1.slice(0, 15),
      );
      expect(updated.strategy).toBe("round_robin");
      view.rerender(<RoutingConfigTab routing={updated} onChange={onChange} />);
      expect(screen.queryByText(/Only the first 15/)).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Trim to 15" }),
      ).not.toBeInTheDocument();
    });

    it.each([1, 3])(
      "keeps legacy repair read-only on the Fallbacks page for org level %i",
      (orgLevel) => {
        permission.orgLevel = orgLevel;
        routing = chain(20, spelling);
        saveRouting.mockClear();
        renderFallbacksSection();
        const trim = screen.getByRole("button", { name: "Trim to 15" });
        expect(trim).toBeDisabled();
        trim.click();
        expect(screen.getByRole("alert")).toHaveTextContent(
          "Only the first 15 fallback models are used",
        );
        expect(
          screen.getAllByRole("button", { name: /^Remove fallback \d+$/ }),
        ).toHaveLength(20);
        expect(saveRouting).not.toHaveBeenCalled();
      },
    );

    it.each([1, 3])(
      "keeps legacy repair read-only in Routing settings for org level %i",
      (orgLevel) => {
        permission.orgLevel = orgLevel;
        const onChange = vi.fn();
        render(
          <RoutingConfigTab
            routing={chain(20, spelling)}
            onChange={onChange}
          />,
        );
        const trim = screen.getByRole("button", { name: "Trim to 15" });
        expect(trim).toBeDisabled();
        trim.click();
        expect(onChange).not.toHaveBeenCalled();
      },
    );

    it.each([15, 16])(
      "keeps a loaded chain of %i visible but disables further additions",
      (length) => {
        routing = chain(length, spelling);
        renderFallbacksSection();
        expect(
          screen.getByRole("button", { name: "Add fallback" }),
        ).toBeDisabled();
        expect(screen.getByRole("status")).toHaveTextContent(
          FALLBACK_LIMIT_MESSAGE,
        );
        expect(
          screen.getAllByRole("button", { name: /^Remove fallback \d+$/ }),
        ).toHaveLength(length);
      },
    );
  },
);
