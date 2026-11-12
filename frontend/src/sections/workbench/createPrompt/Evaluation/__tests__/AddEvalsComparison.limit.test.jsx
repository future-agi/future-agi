import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import AddEvalsComparison from "../AddEvalsComparison";

vi.mock("../../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({
    versions: ["V1", "V2", "V3", "V4"].map((v) => ({
      id: `id-${v}`,
      template_version: v,
      is_draft: false,
    })),
    fetchNextPage: vi.fn(),
    isPending: false,
    isFetchingNextPage: false,
  }),
}));
vi.mock("../context/WorkbenchEvaluationContext", () => ({
  useWorkbenchEvaluationContext: () => ({
    versions: ["V1", "V2", "V3"],
    setVersions: vi.fn(),
    compareOpen: true,
    setCompareOpen: vi.fn(),
  }),
}));
vi.mock("../../VersionHistory/VersionCard", () => ({
  default: ({ version, checkboxMessage, disableCheckbox }) => (
    <div
      data-testid={`card-${version.template_version}`}
      data-disabled={String(Boolean(disableCheckbox))}
    >
      {checkboxMessage}
    </div>
  ),
}));

// FA-PROMPTS-04: the compare limit message is plain and keeps its number.
describe("AddEvalsComparison compare limit copy", () => {
  it("explains the limit of 2 added versions in plain words", () => {
    render(
      <MemoryRouter>
        <AddEvalsComparison />
      </MemoryRouter>,
    );
    const blocked = screen.getByTestId("card-V4");
    expect(blocked).toHaveAttribute("data-disabled", "true");
    expect(blocked).toHaveTextContent(
      "You can add up to 2 versions to compare. Deselect one to select this version.",
    );
    expect(blocked.textContent).not.toMatch(/upto/);
  });

  it("describes the drawer limit in plain words", () => {
    render(
      <MemoryRouter>
        <AddEvalsComparison />
      </MemoryRouter>,
    );
    expect(
      screen.getByText("You can select up to 2 versions to compare."),
    ).toBeInTheDocument();
  });
});
