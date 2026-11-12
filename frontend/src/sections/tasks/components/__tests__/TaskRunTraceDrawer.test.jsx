import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { PROJECT_SOURCE } from "src/utils/constants";
import TaskRunTraceDrawer from "../TaskRunTraceDrawer";

const projectDetails = vi.hoisted(() => ({ current: {} }));
const traceDrawerProps = vi.hoisted(() => ({ current: null }));

vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => projectDetails.current,
}));
vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: (props) => {
    traceDrawerProps.current = props;
    return props.open ? <div data-testid="trace-drawer" /> : null;
  },
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: ({ data }) => (
    <div data-testid="voice-drawer" data-trace-id={data.trace_id} />
  ),
}));

const renderDrawer = () =>
  render(
    <TaskRunTraceDrawer
      open
      onClose={vi.fn()}
      projectId="project-1"
      traceId="trace-1"
      spanId="span-1"
      hasPrev={false}
      hasNext
    />,
  );

describe("TaskRunTraceDrawer", () => {
  beforeEach(() => {
    traceDrawerProps.current = null;
  });

  it("opens the trace drawer on the evaluated span in the task's project", () => {
    projectDetails.current = {
      data: { source: "prototype" },
      isLoading: false,
    };
    renderDrawer();

    expect(screen.getByTestId("trace-drawer")).toBeInTheDocument();
    expect(traceDrawerProps.current).toMatchObject({
      open: true,
      traceId: "trace-1",
      projectId: "project-1",
      initialSpanId: "span-1",
      initialEvalFocus: true,
      hasPrev: false,
      hasNext: true,
    });
  });

  it("opens the voice call drawer for a voice project", () => {
    projectDetails.current = {
      data: { source: PROJECT_SOURCE.SIMULATOR },
      isLoading: false,
    };
    renderDrawer();

    expect(screen.getByTestId("voice-drawer")).toHaveAttribute(
      "data-trace-id",
      "trace-1",
    );
    expect(screen.queryByTestId("trace-drawer")).not.toBeInTheDocument();
  });

  it("renders nothing until the project's kind is known", () => {
    projectDetails.current = { data: undefined, isLoading: true };
    renderDrawer();

    expect(screen.queryByTestId("trace-drawer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("voice-drawer")).not.toBeInTheDocument();
  });
});
