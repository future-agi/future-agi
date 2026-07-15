import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import AutomationRulesTab from "../automation-rules-tab";

const { capturedProps } = vi.hoisted(() => ({ capturedProps: {} }));

vi.mock("ag-grid-react", () => {
  const MockAgGridReact = React.forwardRef((props, _ref) => {
    Object.assign(capturedProps, props);
    return null;
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

  it("keeps every automation-rule row at the same height", () => {
    render(<AutomationRulesTab queueId="queue-1" queue={{}} />);

    expect(capturedProps.getRowHeight).toEqual(expect.any(Function));
    expect(
      [
        { data: { id: "rule-1", name: "Short" } },
        { data: { id: "rule-2", name: "A longer rule name" } },
      ].map(capturedProps.getRowHeight),
    ).toEqual([52, 52]);
  });
});
