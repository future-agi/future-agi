import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import AutomationRulesTab from "../automation-rules-tab";

const { capturedProps } = vi.hoisted(() => ({ capturedProps: {} }));

vi.mock("ag-grid-react", () => {
  const MockAgGridReact = React.forwardRef((props, _ref) => {
    Object.assign(capturedProps, props);
    return (
      <div className="ag-layout-auto-height">
        <div
          className="ag-center-cols-viewport"
          data-testid="automation-rules-grid-viewport"
        />
        <div
          className="ag-center-cols-container"
          data-testid="automation-rules-grid-container"
        />
      </div>
    );
  });
  MockAgGridReact.displayName = "AgGridReactMock";
  return { AgGridReact: MockAgGridReact };
});

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAutomationRules: () => ({ data: [], isLoading: false }),
  useUpdateAutomationRule: () => ({ mutate: vi.fn() }),
  useDeleteAutomationRule: () => ({ mutate: vi.fn() }),
  useEvaluateRule: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/components/custom-dialog", () => ({
  ConfirmDialog: () => null,
}));

vi.mock("src/components/iconify", () => ({
  default: () => null,
}));

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => ({}),
}));

vi.mock("src/theme/ag-theme", () => ({
  AG_THEME_OVERRIDES: { noHeaderBorder: {} },
}));

vi.mock("src/utils/format-time", () => ({
  fDateTime: () => "",
}));

vi.mock("src/styles/clean-data-table.css", () => ({}));

vi.mock("../create-rule-dialog", () => ({
  default: () => null,
  TRIGGER_FREQUENCY_OPTIONS: [],
}));

vi.mock("../edit-rule-dialog", () => ({
  default: () => null,
}));

describe("AutomationRulesTab", () => {
  beforeEach(() => {
    Object.keys(capturedProps).forEach((key) => delete capturedProps[key]);
  });

  it("keeps the auto-height grid body aligned with its fixed-height rows", () => {
    const { getByTestId } = render(
      <AutomationRulesTab queueId="queue-1" queue={{}} />,
    );

    expect(capturedProps.rowHeight).toBe(52);
    expect(capturedProps.getRowHeight).toBeUndefined();
    expect(
      getComputedStyle(getByTestId("automation-rules-grid-viewport")).minHeight,
    ).toBe("52px");
    expect(
      getComputedStyle(getByTestId("automation-rules-grid-container"))
        .minHeight,
    ).toBe("52px");
  });
});
