import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

const traceFilterPanelPropsMock = vi.hoisted(() => vi.fn());

vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: (props) => {
    traceFilterPanelPropsMock(props);
    return <div data-testid="trace-detail-span-filter" />;
  },
}));

import { TraceDetailSpanFilterPanel } from "../TraceDetailDrawerV2";

describe("TraceDetailDrawerV2 span filter adapter", () => {
  it("renders the shared filter panel with span catalog and value identities", () => {
    const onApply = vi.fn();

    render(
      <TraceDetailSpanFilterPanel
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        currentFilters={[]}
        onApply={onApply}
        projectId="project-1"
      />,
    );

    expect(traceFilterPanelPropsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "project-1",
        source: "traces",
        tab: "spans",
        propertyNamespace: "traces",
        attributeSource: "spans",
        isSpansView: true,
        onApply,
      }),
    );
  });
});
