import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import StepPlatformSelect from "../AddIntegrationWizard/StepPlatformSelect";

vi.mock("src/components/svg-color", () => ({ default: () => <span /> }));

describe("Add integration wizard: platform step", () => {
  it("lists observability platforms only, without Slack", () => {
    renderWithRouter(
      <StepPlatformSelect
        data={{}}
        onUpdate={vi.fn()}
        onNext={vi.fn()}
        onSelectSlack={vi.fn()}
      />,
    );
    expect(screen.getByText("Langfuse")).toBeInTheDocument();
    expect(screen.queryByText("Slack")).not.toBeInTheDocument();
  });
});
