import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, userEvent } from "src/utils/test-utils";
import ProviderConfigDialog from "./ProviderConfigDialog";

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["synthetic-chat"],
}));

describe("ProviderConfigDialog GreenPT", () => {
  it("saves GreenPT with its preset URL and explicit model list", async () => {
    const onSave = vi.fn();
    render(<ProviderConfigDialog open onClose={vi.fn()} onSave={onSave} />);
    await userEvent.click(screen.getByLabelText("Provider"));
    await userEvent.click(screen.getByRole("option", { name: "GreenPT" }));
    expect(screen.getByLabelText("Base URL").value).toBe("https://api.greenpt.ai");
    fireEvent.change(screen.getByLabelText("API Key"), {
      target: { value: "synthetic-greenpt-key" },
    });
    await userEvent.type(screen.getByLabelText("Models"), "synthetic-chat{enter}");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith("greenpt", expect.objectContaining({
      base_url: "https://api.greenpt.ai",
      api_key: "synthetic-greenpt-key",
      models: ["synthetic-chat"],
    }));
  });
});
