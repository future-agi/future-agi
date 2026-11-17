import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import AddIntegrationWizard from "../AddIntegrationWizard";
import { LISTED_PLATFORMS, PLATFORMS } from "../constants";

// The Add Integration stepper names only steps the platform picked goes
// through. Before a platform is picked it names the steps every offered
// platform shares, so it never shows the Langfuse import's "Project" step
// while Langfuse is not offered.

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
}));

vi.mock("../AddIntegrationWizard/StepPlatformSelect", () => ({
  default: () => null,
}));
vi.mock("../AddIntegrationWizard/StepCredentials", () => ({
  default: () => null,
}));

const stepper = () =>
  screen
    .getAllByText(/^(Platform|Credentials|Project|Sync Settings)$/)
    .map((el) => el.textContent);

const renderWizard = (props = {}) =>
  render(
    <AddIntegrationWizard
      open
      platforms={LISTED_PLATFORMS}
      onClose={vi.fn()}
      {...props}
    />,
  );

describe("Add Integration stepper", () => {
  it("names only the steps every offered platform shares before a pick", () => {
    renderWizard();
    expect(stepper()).toEqual(["Platform", "Credentials"]);
  });

  it("names the export steps for an export platform", () => {
    renderWizard({ initialPlatform: "datadog" });
    expect(stepper()).toEqual(["Platform", "Credentials", "Sync Settings"]);
  });

  it("names Linear's two steps", () => {
    renderWizard({ initialPlatform: "linear" });
    expect(stepper()).toEqual(["Platform", "Credentials"]);
  });

  it("keeps the import's Project step for Langfuse", () => {
    renderWizard({
      initialPlatform: "langfuse",
      platforms: PLATFORMS,
    });
    expect(stepper()).toEqual([
      "Platform",
      "Credentials",
      "Project",
      "Sync Settings",
    ]);
  });
});
