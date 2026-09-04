import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { parseTimeoutSeconds } from "./utils";
import AddProviderDialog from "./AddProviderDialog";

describe("parseTimeoutSeconds", () => {
  it("normalizes Gateway provider timeout text to integer seconds", () => {
    expect(parseTimeoutSeconds("45")).toBe(45);
    expect(parseTimeoutSeconds("45s")).toBe(45);
    expect(parseTimeoutSeconds("2m")).toBe(120);
    expect(parseTimeoutSeconds("1500ms")).toBe(2);
    expect(parseTimeoutSeconds("")).toBeNull();
    expect(parseTimeoutSeconds("soon")).toBeNull();
    expect(parseTimeoutSeconds("0s")).toBeNull();
  });
});

vi.mock("./hooks/useGatewayConfig", () => ({
  useUpdateProvider: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useFetchProviderModels: () => ({ mutate: vi.fn(), isPending: false }),
}));

// Regression coverage for #2281: switching named providers must always land
// on that provider's own upstream adapter, never a format left over from
// whichever provider was previously selected.
describe("AddProviderDialog — API format follows the selected provider", () => {
  const providerSelect = () =>
    screen.getByRole("combobox", { name: "Provider" });
  const apiFormatSelect = () =>
    screen.getByRole("combobox", { name: "API Format" });

  it("switches api_format to anthropic when Anthropic is selected from the OpenAI default", async () => {
    const user = userEvent.setup();
    render(<AddProviderDialog open onClose={vi.fn()} gatewayId="gw-1" />);

    await user.click(providerSelect());
    await user.click(await screen.findByRole("option", { name: "Anthropic" }));

    expect(apiFormatSelect()).toHaveTextContent("anthropic");
  });

  it("switches api_format to azure, and offers azure as a selectable option", async () => {
    const user = userEvent.setup();
    render(<AddProviderDialog open onClose={vi.fn()} gatewayId="gw-1" />);

    await user.click(providerSelect());
    await user.click(await screen.findByRole("option", { name: "Azure OpenAI" }));

    expect(apiFormatSelect()).toHaveTextContent("azure");
  });

  it("does not retain a stale format when switching back from Anthropic to OpenAI", async () => {
    const user = userEvent.setup();
    render(<AddProviderDialog open onClose={vi.fn()} gatewayId="gw-1" />);

    await user.click(providerSelect());
    await user.click(await screen.findByRole("option", { name: "Anthropic" }));
    expect(apiFormatSelect()).toHaveTextContent("anthropic");

    await user.click(providerSelect());
    await user.click(await screen.findByRole("option", { name: "OpenAI" }));
    expect(apiFormatSelect()).toHaveTextContent("openai");
  });

  it("switches api_format to google when Google is selected from the OpenAI default", async () => {
    const user = userEvent.setup();
    render(<AddProviderDialog open onClose={vi.fn()} gatewayId="gw-1" />);

    await user.click(providerSelect());
    await user.click(
      await screen.findByRole("option", { name: "Google (Gemini)" }),
    );

    expect(apiFormatSelect()).toHaveTextContent("google");
  });

  it("keeps the current format when switching to Custom if Custom still supports it", async () => {
    const user = userEvent.setup();
    render(<AddProviderDialog open onClose={vi.fn()} gatewayId="gw-1" />);

    await user.click(providerSelect());
    await user.click(await screen.findByRole("option", { name: "Anthropic" }));
    expect(apiFormatSelect()).toHaveTextContent("anthropic");

    await user.click(providerSelect());
    await user.click(
      await screen.findByRole("option", { name: "Custom / Self-hosted" }),
    );

    expect(apiFormatSelect()).toHaveTextContent("anthropic");
  });
});
