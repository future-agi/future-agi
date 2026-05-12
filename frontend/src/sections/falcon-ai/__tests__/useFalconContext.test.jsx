import { describe, it, expect, beforeEach } from "vitest";
import { renderWithRouter, screen } from "src/utils/test-utils";
import { useFalconContext } from "../hooks/useFalconContext";
import useFalconStore from "../store/useFalconStore";

function ContextProbe() {
  const context = useFalconContext();
  return <pre data-testid="context">{JSON.stringify(context)}</pre>;
}

function readContext() {
  return JSON.parse(screen.getByTestId("context").textContent);
}

beforeEach(() => {
  useFalconStore.getState().resetAll();
});

describe("useFalconContext", () => {
  it("treats observe project pages as project context", () => {
    renderWithRouter(<ContextProbe />, {
      route: "/dashboard/observe/project-123",
    });

    expect(readContext()).toMatchObject({
      page: "tracing",
      entity_type: "project",
      entity_id: "project-123",
    });
  });

  it("extracts trace id from full trace pages", () => {
    renderWithRouter(<ContextProbe />, {
      route: "/dashboard/observe/project-123/trace/trace-456",
    });

    expect(readContext()).toMatchObject({
      page: "tracing",
      entity_type: "trace",
      entity_id: "trace-456",
      project_id: "project-123",
    });
  });

  it("uses active drawer context over the route context", () => {
    useFalconStore.getState().setActivePageContext({
      page: "tracing",
      entity_type: "trace",
      entity_id: "drawer-trace",
      project_id: "project-123",
      source: "trace_drawer",
    });

    renderWithRouter(<ContextProbe />, {
      route: "/dashboard/observe/project-123",
    });

    expect(readContext()).toMatchObject({
      entity_type: "trace",
      entity_id: "drawer-trace",
      project_id: "project-123",
      source: "trace_drawer",
    });
  });
});
