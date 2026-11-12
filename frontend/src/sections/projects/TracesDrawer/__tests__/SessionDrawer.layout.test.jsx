/* eslint-disable react/prop-types */
// Session drawer (Sessions > open a session), G4:
// (a) a short assistant reply ("Yes.") shrank its bubble so far that the copy
//     button, drawn in the bubble's top-right corner, covered the role label;
// (b) the drawer stops short of the left edge on purpose, but its backdrop was
//     invisible, so the strip of sidebar left showing looked live and sliced;
// (c) at 1024 px the "Trace ID:" label wrapped onto two lines.
// Layout is not measured here: the tests pin the styles and classes the fix
// sets, and that nothing is removed.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import { backdropClasses } from "@mui/material";

import ShowTrace from "../ShowTrace";
import TracesDrawer from "../TracesDrawer";
import { resetTraceDrawerStore } from "../useTraceDrawerStore";

const h = vi.hoisted(() => ({
  sessionQuery: {
    data: {
      pages: [
        {
          data: {
            result: {
              response: [],
              session_metadata: { session_id: "session-1" },
            },
          },
        },
      ],
    },
    isPlaceholderData: false,
    isLoading: false,
    isError: false,
    isFetching: false,
    isFetchingNextPage: false,
    error: null,
    refetch: () => {},
    fetchNextPage: () => {},
  },
}));

vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useInfiniteQuery: () => h.sessionQuery,
}));
vi.mock("src/api/project/trace-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetTraceDetail: () => ({ data: undefined }),
}));
vi.mock(
  "src/components/traceDetail/TraceDetailDrawerV2",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock("src/contexts/WorkspaceContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/components/InlineAnnotator", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("src/sections/common/CellMarkdown", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ text }) => <span>{text}</span>,
}));
// Icons are stubbed, so no glyph reaches an accessible name.
vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("src/components/svg-color", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <span />,
}));
vi.mock("../Header", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../Filters", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

const TRACE_ID = "7a8c1ccc-560c-4d93-ac8e-ac540ef784ab";

const renderTurn = () =>
  render(
    <ShowTrace
      traceId={TRACE_ID}
      human="Can I get a refund?"
      ai="Yes."
      systemMetrics={{ total_latency_ms: 1200 }}
      evaluationMetrics={{}}
      activeSessionId="session-1"
    />,
  );

const renderDrawer = (onClose = vi.fn()) =>
  render(
    <TracesDrawer
      open
      onClose={onClose}
      rowData={{ session_id: "session-1" }}
    />,
  );

// The role label sits in its own row at the top of the bubble.
const labelRow = (role) =>
  screen.getByText(role, { exact: true }).parentElement;

const drawerBackdrop = () =>
  document.querySelector(`.MuiDrawer-root .${backdropClasses.root}`);

describe("session drawer layout (G4)", () => {
  afterEach(() => {
    resetTraceDrawerStore();
  });

  it("keeps the AI label clear of the copy button on a short reply", () => {
    renderTurn();

    // 24px copy button plus an 8px gap.
    const style = window.getComputedStyle(labelRow("AI"));
    expect(style.paddingRight).toBe("32px");
  });

  it("keeps the Human label clear of the copy button too", () => {
    renderTurn();

    const style = window.getComputedStyle(labelRow("Human"));
    expect(style.paddingRight).toBe("32px");
  });

  it("keeps each bubble's label, copy button and whole text", () => {
    renderTurn();

    const aiBubble = labelRow("AI").parentElement;
    expect(within(aiBubble).getByText("Yes.")).toBeInTheDocument();
    expect(within(aiBubble).getAllByRole("button")).toHaveLength(1);

    const humanBubble = labelRow("Human").parentElement;
    expect(
      within(humanBubble).getByText("Can I get a refund?"),
    ).toBeInTheDocument();
    expect(within(humanBubble).getAllByRole("button")).toHaveLength(1);
  });

  it("keeps the Trace ID label on one line", () => {
    renderTurn();

    const style = window.getComputedStyle(screen.getByText("Trace ID:"));
    expect(style.whiteSpace).toBe("nowrap");
    expect(style.flexShrink).toBe("0");
  });

  it("still shows the whole trace id beside the label", () => {
    renderTurn();

    const label = screen.getByText("Trace ID:");
    const id = screen.getByText(TRACE_ID, { exact: true });
    expect(id.parentElement).toBe(label.parentElement);
  });

  it("dims the strip of page the drawer leaves uncovered", () => {
    renderDrawer();

    const backdrop = drawerBackdrop();
    expect(backdrop).not.toBeNull();
    expect(backdrop).not.toHaveClass(backdropClasses.invisible);
  });

  it("still closes when the uncovered strip is clicked", () => {
    const onClose = vi.fn();
    renderDrawer(onClose);

    fireEvent.click(drawerBackdrop());
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
