import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CompareContainer from "./CompareContainer";
import { usePromptWorkbenchContext } from "../WorkbenchContext";

vi.mock("../WorkbenchContext", () => ({
  usePromptWorkbenchContext: vi.fn(),
}));

vi.mock("../hooks/use-extract-all-variables", () => ({
  useExtractAllVariables: () => [],
}));

vi.mock("../promptActions/PromptActions", () => ({
  default: () => <div data-testid="prompt-actions" />,
}));

vi.mock("../Evaluation", () => ({
  default: () => <div data-testid="evaluation" />,
}));

vi.mock("../Metrics/Metrics", () => ({
  default: () => <div data-testid="metrics" />,
}));

vi.mock("./CompareInputs", () => ({
  default: () => <div data-testid="compare-inputs" />,
}));

vi.mock("./CompareOutputs", () => ({
  default: () => <div data-testid="compare-outputs" />,
}));

vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <div data-testid="resizable-panels">
      {leftPanel}
      {rightPanel}
    </div>
  ),
}));

vi.mock("src/components/VariableDrawer/VariableDrawer", () => ({
  default: () => <div data-testid="variable-drawer" />,
}));

vi.mock(
  "src/components/VariableDrawer/ImportDataset/ImportDatasetDrawer",
  () => ({
    default: () => <div data-testid="import-dataset-drawer" />,
  }),
);

const baseContext = {
  currentTab: "Playground",
  isImportDatasetDrawerOpen: false,
  prompts: [],
  setImportDatasetDrawerOpen: vi.fn(),
  setVariableData: vi.fn(),
  setVariableDrawerOpen: vi.fn(),
  templateFormat: "mustache",
  variableData: {},
  variableDrawerOpen: false,
};

describe("CompareContainer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePromptWorkbenchContext.mockReturnValue(baseContext);
  });

  it("renders the metrics surface for compared prompt versions", () => {
    usePromptWorkbenchContext.mockReturnValue({
      ...baseContext,
      currentTab: "Metrics",
    });

    render(<CompareContainer />);

    expect(screen.getByTestId("prompt-actions")).toBeVisible();
    expect(screen.getByTestId("metrics")).toBeVisible();
    expect(screen.queryByTestId("evaluation")).toBeNull();
    expect(screen.queryByTestId("resizable-panels")).toBeNull();
  });
});
