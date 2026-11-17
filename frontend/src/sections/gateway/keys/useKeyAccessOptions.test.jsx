import React from "react";
import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { useKeyAccessOptions } from "./useKeyAccessOptions";
import CreateKeyDialog from "./CreateKeyDialog";
import EditKeyDialog from "./EditKeyDialog";

const state = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: { providers: {
    openai: { models: ["gpt-4.1"] },
    custom: { models: ["gpt-4.1", "vendor/chat"] },
  } } }),
}));
vi.mock("./hooks/useApiKeys", () => ({
  useCreateApiKey: () => ({ mutate: state.create }),
  useUpdateApiKey: () => ({ mutate: state.update }),
}));
vi.mock("./AllowedProvidersField", () => ({ default: () => null }));

describe("R3-KEYS-ONBOARDING-02 exact request model IDs", () => {
  it("offers qualified console IDs and distinct bare IDs, including slash-containing model names", () => {
    const { result } = renderHook(() => useKeyAccessOptions("default"));
    expect(result.current.modelOptions).toEqual([
      "custom/gpt-4.1", "custom/vendor/chat", "gpt-4.1", "openai/gpt-4.1", "vendor/chat",
    ]);
  });

  it("submits exactly the selected qualified grant on create", async () => {
    const user = userEvent.setup();
    render(<CreateKeyDialog open gatewayId="default" onClose={vi.fn()} onCreated={vi.fn()} />);
    await user.type(screen.getByRole("textbox", { name: /Name/ }), "CI key");
    await user.click(screen.getByRole("combobox", { name: "Allowed models" }));
    await user.click(screen.getByRole("option", { name: "openai/gpt-4.1" }));
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(state.create).toHaveBeenCalledWith(expect.objectContaining({
      allowed_models: ["openai/gpt-4.1"],
    }), expect.any(Object));
  });

  it("does not expand an existing bare grant when editing another field", async () => {
    const user = userEvent.setup();
    render(<EditKeyDialog open gatewayId="default" onClose={vi.fn()} keyData={{
      id: "k1", name: "CI key", allowedModels: ["gpt-4.1"], allowedProviders: [],
    }} />);
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(state.update).toHaveBeenCalledWith(expect.objectContaining({
      allowed_models: ["gpt-4.1"],
    }), expect.any(Object));
  });
});
