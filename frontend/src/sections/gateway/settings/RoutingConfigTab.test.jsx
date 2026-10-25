import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { buildConfigPatch } from "../hooks/useConfigPatch";
import RoutingConfigTab from "./RoutingConfigTab";
import ModelMapConfigTab from "./ModelMapConfigTab";

// Org Config > Routing shows the same effective fallback switch as the
// Fallbacks page (unset with chains is on, explicit false is off). It offers
// no Default Model control and no Conditional Routes editor (the gateway
// applies neither), and stored values stay as they are. The Model Map tab
// states the rule the gateway runs.

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["m1", "m2"],
}));

const fallbackSwitch = () =>
  screen.getByRole("checkbox", { name: "Fallback Enabled" });

describe("Org Config > Routing", () => {
  it("shows the fallback switch on when unset with chains", () => {
    render(
      <RoutingConfigTab
        routing={{ model_fallbacks: { m1: ["m2"] } }}
        onChange={vi.fn()}
      />,
    );
    expect(fallbackSwitch()).toBeChecked();
  });

  it("shows an explicit false as off", () => {
    render(
      <RoutingConfigTab
        routing={{ fallback_enabled: false, model_fallbacks: { m1: ["m2"] } }}
        onChange={vi.fn()}
      />,
    );
    expect(fallbackSwitch()).not.toBeChecked();
  });

  it("shows the fallback switch off when unset without chains", () => {
    render(<RoutingConfigTab routing={{}} onChange={vi.fn()} />);
    expect(fallbackSwitch()).not.toBeChecked();
  });

  it("offers neither Default Model nor Conditional Routes", () => {
    render(
      <RoutingConfigTab
        routing={{
          default_model: "m1",
          conditional_routes: [{ name: "r", field: "model", value: "m1" }],
        }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText("Default Model")).not.toBeInTheDocument();
    expect(screen.queryByText("Default Model")).not.toBeInTheDocument();
    expect(screen.queryByText("Conditional Routes")).not.toBeInTheDocument();
  });

  it("keeps stored default_model and conditional_routes on a change", async () => {
    const onChange = vi.fn();
    const stored = {
      default_model: "m1",
      conditional_routes: [{ name: "r", field: "model", value: "m1" }],
      model_fallbacks: { m1: ["m2"] },
    };
    render(<RoutingConfigTab routing={stored} onChange={onChange} />);
    fallbackSwitch().click();
    expect(onChange).toHaveBeenCalled();
    const next = onChange.mock.calls[0][0];
    expect(next.default_model).toBe("m1");
    expect(next.conditional_routes).toEqual(stored.conditional_routes);
    expect(next.fallback_enabled).toBe(false);
  });
});

// The caption states the one way a customer key's request leaves the org's
// providers (a model fallback to a managed model, with managed models on),
// and an unset strategy shows what runs (the first of the org's providers
// by ID).
describe("Org Config > Routing strategy and key scope", () => {
  const strategyButton = (name) => screen.getByRole("button", { name });

  it("says a model fallback can go to a managed model", () => {
    render(<RoutingConfigTab routing={{}} onChange={vi.fn()} />);
    expect(
      screen.getByText(
        /a model fallback can go to a managed model when your organization turned managed models on/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/for every strategy, retry, failover and fallback/i),
    ).not.toBeInTheDocument();
  });

  it("shows an unset strategy as the first provider by ID", () => {
    render(<RoutingConfigTab routing={{}} onChange={vi.fn()} />);
    expect(strategyButton("None: first provider by ID")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(strategyButton("Round Robin")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("shows a stored strategy as chosen", () => {
    render(
      <RoutingConfigTab
        routing={{ strategy: "weighted" }}
        onChange={vi.fn()}
      />,
    );
    expect(strategyButton("Weighted")).toHaveAttribute("aria-pressed", "true");
    expect(strategyButton("None: first provider by ID")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("removes the stored strategy when None is chosen", () => {
    const onChange = vi.fn();
    render(
      <RoutingConfigTab
        routing={{ strategy: "weighted", model_fallbacks: { m1: ["m2"] } }}
        onChange={onChange}
      />,
    );
    strategyButton("None: first provider by ID").click();
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0];
    expect(next).not.toHaveProperty("strategy");
    expect(next.model_fallbacks).toEqual({ m1: ["m2"] });
    // OrgConfigEditor saves the draft through update-config as a merge patch
    // of what changed (buildConfigPatch): the dropped key goes as null, which
    // deletes the stored strategy.
    const saved = {
      routing: { strategy: "weighted", model_fallbacks: { m1: ["m2"] } },
    };
    expect(buildConfigPatch(saved, { routing: next }, ["routing"])).toEqual({
      routing: { strategy: null },
    });
  });
});

describe("Org Config > Model Map", () => {
  it("states the rule the gateway runs for the org's keys", () => {
    render(<ModelMapConfigTab modelMap={{}} onChange={vi.fn()} />);
    expect(
      screen.getByText(/one of your organization's own providers/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /lists exactly one model, the request is sent as that model/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/sorted by provider id/i)).toBeInTheDocument();
  });
});
