import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithRouter, screen } from "src/utils/test-utils";
import AddIntegrationWizard from "../AddIntegrationWizard";

// The Add Integration drawer covers the page with a transparent backdrop. A
// click on the page behind it keeps the wizard open, with its step and what
// was typed into it. Escape and the close button still close it.

vi.mock("../AddIntegrationWizard/StepPlatformSelect", () => ({
  default: () => null,
}));
vi.mock("../AddIntegrationWizard/StepCredentials", () => ({
  default: () => <input aria-label="API key" defaultValue="typed" />,
}));
vi.mock("../AddIntegrationWizard/StepProjectMapping", () => ({
  default: () => null,
}));
vi.mock("src/api/integrations", () => ({
  useCreateConnection: () => ({
    mutate: vi.fn(),
    isPending: false,
    error: null,
  }),
}));

const openWizard = () => {
  const onClose = vi.fn();
  renderWithRouter(
    <AddIntegrationWizard
      open
      platforms={[]}
      initialPlatform="datadog"
      onClose={onClose}
      onSelectSlack={vi.fn()}
    />,
    { route: "/dashboard/settings/integrations" },
  );
  return onClose;
};

describe("Add Integration drawer and the page behind it", () => {
  it("stays open, on its step, when the page behind it is clicked", () => {
    const onClose = openWizard();

    fireEvent.click(document.querySelector(".MuiBackdrop-root"));

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "API key" })).toHaveValue(
      "typed",
    );
  });

  it("closes on Escape", () => {
    const onClose = openWizard();

    fireEvent.keyDown(screen.getByRole("textbox", { name: "API key" }), {
      key: "Escape",
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes from its close button", () => {
    const onClose = openWizard();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
