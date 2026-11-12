import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import AddEvalsComparison from "../AddEvalsComparison";

vi.mock("../../hooks/use-prompt-versions", () => ({
  usePromptVersions: () => ({
    versions: ["V1", "V2"].map((v) => ({
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
    versions: ["V1"],
    setVersions: vi.fn(),
    compareOpen: true,
    setCompareOpen: vi.fn(),
  }),
}));
vi.mock("../../VersionHistory/VersionCard", () => ({
  default: ({ version }) => (
    <div data-testid={`card-${version.template_version}`} />
  ),
}));

// The Evaluation tab's compare drawer was a fixed 640 px, so
// on a phone its title and version list started off screen.
describe("Evaluation compare drawer on narrow screens", () => {
  it("keeps its 640 px width but never grows past the screen", () => {
    render(
      <MemoryRouter>
        <AddEvalsComparison />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("card-V2")).toBeInTheDocument();
    const paper = document.querySelector(".MuiDrawer-paper");
    expect(getComputedStyle(paper).width).toBe("640px");
    expect(getComputedStyle(paper).maxWidth).toBe("100vw");
  });

  it("names its close button", () => {
    render(
      <MemoryRouter>
        <AddEvalsComparison />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });
});
