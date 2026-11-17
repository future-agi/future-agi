import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import ModelMapConfigTab from "./ModelMapConfigTab";

let providerQuery;
const refetch = vi.fn();
const changed = vi.fn();
const readProviders = vi.fn();
vi.mock("../hooks/useAvailableModels", () => ({ useAvailableModels: () => ["model-a"] }));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: (id) => { readProviders(id); return { ...providerQuery, refetch }; },
}));
vi.mock("../hooks/useGatewayAdmin", () => ({ useGatewayAdmin: () => ({ canEdit: true }) }));

beforeEach(() => {
  vi.clearAllMocks();
  providerQuery = { data: { providers: [
    { id: "provider-a", display_name: "Team primary", pushed: true, models: ["model-a"] },
    { id: "provider-b", display_name: "Missing key", pushed: false, models: ["model-b", "model-c"] },
  ] }, isLoading: false };
});

describe("R3-CONTROLS-12 configured model-map targets", () => {
  it("uses the exact configured provider ID and preserves unknown saved targets", async () => {
    render(<ModelMapConfigTab gatewayId="gw-1" modelMap={{ existing: "unknown-id" }} onChange={changed} />);
    expect(readProviders).toHaveBeenCalledWith("gw-1");
    expect(screen.getByText("unknown-id")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText("Model name (e.g. gpt-4o)"), "fast");
    await userEvent.click(screen.getByLabelText("Configured provider"));
    expect(screen.getByRole("option", { name: /Team primary.*provider-a.*1 configured model/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Missing key.*provider-b/ })).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(screen.getByRole("option", { name: /Team primary.*provider-a/ }));
    expect(screen.getByText(/This alias will be sent as model-a/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(changed).toHaveBeenCalledWith({ existing: "unknown-id", fast: "provider-a" });
  });

  it("distinguishes unavailable provider reads from an empty configured list", async () => {
    providerQuery = { isError: true };
    const view = render(<ModelMapConfigTab gatewayId="gw-1" modelMap={{}} onChange={changed} />);
    expect(screen.getByText(/Could not load configured providers/)).toBeInTheDocument();
    expect(screen.queryByText(/No configured providers/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry providers" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    providerQuery = { data: { providers: [] }, isLoading: false };
    view.rerender(<ModelMapConfigTab gatewayId="gw-1" modelMap={{}} onChange={changed} />);
    expect(screen.getByText(/No configured providers/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    expect(changed).not.toHaveBeenCalled();
  });
});
