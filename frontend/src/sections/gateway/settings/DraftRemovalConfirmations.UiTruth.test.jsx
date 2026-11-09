import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";
import FallbackChainEditor from "./FallbackChainEditor";
import GuardrailConfigTab from "./GuardrailConfigTab";
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["primary", "backup"],
}));

describe("named configuration removals", () => {
  it("confirms a fallback chain before removing it from the draft", () => {
    const remove = vi.fn();
    render(
      <FallbackChainEditor
        primaryModel="primary"
        fallbacks={["backup"]}
        onChange={vi.fn()}
        onRemoveChain={remove}
      />,
    );
    fireEvent.click(screen.getAllByRole("button").at(-1));
    expect(remove).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toHaveTextContent("primary");
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Click Save & Activate to apply the removal.",
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Remove",
      }),
    );
    expect(remove).toHaveBeenCalledTimes(1);
  });
  it("confirms the named fallback model before removing it", () => {
    const change = vi.fn();
    render(
      <FallbackChainEditor
        primaryModel="primary"
        fallbacks={["backup"]}
        onChange={change}
      />,
    );
    fireEvent.click(screen.getAllByRole("button")[0]);
    expect(change).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toHaveTextContent("backup");
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Remove",
      }),
    );
    expect(change).toHaveBeenCalledWith("primary", []);
  });
  it("confirms the named saved check before removing its configuration", () => {
    const change = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{
          checks: {
            "keyword-blocklist": {
              enabled: true,
              config: { keywords: ["example"] },
            },
          },
        }}
        onChange={change}
        nodePipeline={{ enabled: true }}
      />,
    );
    expect(screen.queryByRole("button", { name: "Reset" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(change).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Remove configuration"),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Keyword Blocklist");
    expect(dialog).toHaveTextContent(
      "Click Save & Activate to apply the removal.",
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Remove",
      }),
    );
    expect(change).toHaveBeenCalledWith(
      expect.objectContaining({ checks: {} }),
    );
  });
});
