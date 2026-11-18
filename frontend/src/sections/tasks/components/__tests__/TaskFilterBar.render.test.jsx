import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";

const testState = vi.hoisted(() => ({ formFilters: [] }));
const traceFilterPanelPropsMock = vi.hoisted(() => vi.fn());
const useTraceFilterPropertiesMock = vi.hoisted(() => vi.fn());
const useDashboardFilterValuesMock = vi.hoisted(() => vi.fn());

vi.mock("react-hook-form", () => ({
  useWatch: () => testState.formFilters,
}));

vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: (props) => {
    traceFilterPanelPropsMock(props);
    return <div data-testid="task-trace-filter-panel" />;
  },
  findTraceFilterProperty: (properties, filter) =>
    properties.find(
      (property) =>
        property.registryId === filter.registryId ||
        property.id === filter.field,
    ),
  parseMapFilterValue: (value) => value,
  useTraceFilterProperties: (...args) => useTraceFilterPropertiesMock(...args),
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: (...args) => useDashboardFilterValuesMock(...args),
}));

import TaskFilterBar from "../TaskFilterBar";
import { SESSION_RULE_FILTER_FIELDS } from "src/sections/annotations/queues/constants";

describe("TaskFilterBar rendered catalog adapters", () => {
  beforeEach(() => {
    testState.formFilters = [];
    traceFilterPanelPropsMock.mockClear();
    useTraceFilterPropertiesMock.mockReset();
    useTraceFilterPropertiesMock.mockReturnValue({ data: [] });
    useDashboardFilterValuesMock.mockReset();
    useDashboardFilterValuesMock.mockReturnValue({ data: [] });
  });

  it("does not eagerly read labels and keeps voice panel identities aligned", () => {
    render(
      <TaskFilterBar
        control={{}}
        setValue={vi.fn()}
        projectId="voice-project"
        rowType="voiceCalls"
        isSimulator
      />,
    );

    expect(useTraceFilterPropertiesMock).toHaveBeenCalledWith(
      "voice-project",
      expect.objectContaining({
        enabled: false,
        isSimulator: true,
        sourceScope: "voice_calls",
      }),
    );
    expect(traceFilterPanelPropsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "voice-project",
        source: "traces",
        tab: "voiceCalls",
        attributeSource: "spans",
        isSimulator: true,
      }),
    );
  });

  it("resolves persisted session labels with the panel catalog and value scopes", () => {
    testState.formFilters = [
      {
        property: "annotator",
        propertyId: "annotator",
        fieldCategory: "annotation",
        apiColType: "ANNOTATION",
        filterConfig: {
          filterType: "annotator",
          filterOp: "in",
          filterValue: ["user-1"],
          colType: "ANNOTATION",
        },
      },
    ];

    render(
      <TaskFilterBar
        control={{}}
        setValue={vi.fn()}
        projectId="session-project"
        rowType="sessions"
      />,
    );

    expect(useTraceFilterPropertiesMock).toHaveBeenCalledWith(
      "session-project",
      expect.objectContaining({
        enabled: true,
        isSimulator: false,
        sourceScope: "spans",
      }),
    );
    expect(useDashboardFilterValuesMock).toHaveBeenCalledWith(
      expect.objectContaining({
        metricName: "annotator",
        metricType: "annotation_metric",
        projectIds: ["session-project"],
        source: "sessions",
        enabled: true,
      }),
    );
    expect(traceFilterPanelPropsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: "session-project",
        source: "sessions",
        tab: null,
        attributeSource: "spans",
        filterFields: SESSION_RULE_FILTER_FIELDS,
      }),
    );
  });
});
