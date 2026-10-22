/* eslint-disable react/prop-types */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLLMTracingStore } from "src/sections/projects/LLMTracing/states";

// The real call drawer, Traces panel and trace-drawer header run here; only the
// data hooks and the two heavy drawers' bodies are stood in for.
const mocks = vi.hoisted(() => ({
  traces: { projectId: null, traces: [], isError: false },
}));

vi.mock("src/components/iconify", () => ({ default: () => <span /> }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCallDetail: () => ({ callDetail: null, isLoading: false }),
    useCallExecutionV3Detail: () => ({
      data: { id: "voice-1", transcript: [] },
      isPending: false,
    }),
    useCallTraces: () => mocks.traces,
  };
});

// Closes the way the product's trace drawer does: its header listens on the
// document for Escape.
vi.mock("src/sections/projects/LLMTracing/LLMTracingTraceDetailDrawer", async () => {
  const { default: DrawerHeader } = await import(
    "src/components/traceDetail/DrawerHeader"
  );
  const { useLLMTracingStore: store } = await import(
    "src/sections/projects/LLMTracing/states"
  );
  return {
    default: function TraceDrawerStub() {
      const open = store((state) => state.traceDetailDrawerOpen);
      const setOpen = store((state) => state.setTraceDetailDrawerOpen);
      return open ? (
        <div data-testid="trace-drawer">
          <DrawerHeader
            open
            traceId={open.traceId}
            onClose={() => setOpen(null)}
          />
        </div>
      ) : null;
    },
  };
});

vi.mock("src/components/VoiceDetailDrawerV2", async () => {
  const { useState } = await import("react");
  return {
    default: function VoiceStub({
      leftPanelTabs,
      onPrev,
      onNext,
      hasPrev,
      hasNext,
    }) {
      const [tab, setTab] = useState("transcript");
      const active = (leftPanelTabs ?? []).find((t) => t.value === tab);
      return (
        <div data-testid="voice-drawer">
          <button type="button" onClick={() => setTab("transcript")}>
            transcript
          </button>
          {(leftPanelTabs ?? []).map((t) => (
            <button key={t.value} type="button" onClick={() => setTab(t.value)}>
              {t.label}
            </button>
          ))}
          <button type="button" onClick={onPrev} disabled={!hasPrev}>
            prev
          </button>
          <button type="button" onClick={onNext} disabled={!hasNext}>
            next
          </button>
          {active?.content}
        </div>
      );
    },
  };
});

const { default: CallDrawer } = await import("../CallDrawer");

const ROWS = [{ traceId: "trace-1", name: "book_ride" }];
const chatTask = { id: "chat-1", status: "completed", simulationCallType: "text" };
const voiceTask = { id: "voice-1", simulationCallType: "voice" };
const URL_KEY = "traceDetailDrawerOpen";
const urlHasTrace = () => new URLSearchParams(window.location.search).has(URL_KEY);

const renderChat = (props = {}) =>
  render(
    <CallDrawer
      task={chatTask}
      executionId="run-1"
      agentType="text"
      onClose={() => {}}
      {...props}
    />,
  );

const renderVoice = (props = {}) =>
  render(
    <CallDrawer
      task={voiceTask}
      executionId="run-1"
      agentType="voice"
      onClose={() => {}}
      {...props}
    />,
  );

beforeEach(() => {
  mocks.traces = { projectId: "hidden-project", traces: ROWS, isError: false };
});

afterEach(() => {
  useLLMTracingStore.getState().setTraceDetailDrawerOpen(null);
});

describe("CallDrawer — a call with no trace", () => {
  beforeEach(() => {
    mocks.traces = { projectId: null, traces: [], isError: false };
  });

  it("shows the Traces tab on a chat call, saying only that there is no trace", async () => {
    const user = userEvent.setup();
    renderChat();

    await user.click(screen.getByRole("tab", { name: "Traces" }));

    expect(screen.getByText("No trace for this call")).toBeInTheDocument();
    expect(screen.queryByText(/code didn't send/)).not.toBeInTheDocument();
  });

  it("shows the Traces tab on a voice call, saying only that there is no trace", async () => {
    const user = userEvent.setup();
    renderVoice();

    await user.click(screen.getByRole("button", { name: "Traces" }));

    expect(screen.getByText("No trace for this call")).toBeInTheDocument();
    expect(screen.queryByText(/code didn't send/)).not.toBeInTheDocument();
  });
});

describe("CallDrawer — a trace key left in the URL", () => {
  const leaveTraceInUrl = () =>
    useLLMTracingStore
      .getState()
      .setTraceDetailDrawerOpen({ traceId: "trace-1", projectId: "hidden-project" });

  it("leaves a chat call's arrows working on Transcript and clears the key", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    leaveTraceInUrl();
    expect(urlHasTrace()).toBe(true);

    renderChat({ hasNext: true, onNext });

    expect(urlHasTrace()).toBe(false);
    expect(useLLMTracingStore.getState().traceDetailDrawerOpen).toBeNull();
    await user.keyboard("{ArrowDown}");
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it("leaves a voice call's arrows enabled on Transcript and clears the key", () => {
    leaveTraceInUrl();

    renderVoice({ hasPrev: true, hasNext: true });

    expect(urlHasTrace()).toBe(false);
    expect(screen.getByRole("button", { name: "next" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "prev" })).toBeEnabled();
  });
});

describe("CallDrawer — a trace open over the call", () => {
  it("keeps the arrow keys from stepping a chat call, and clears the key on leaving the tab", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    renderChat({ hasNext: true, onNext });

    await user.click(screen.getByRole("tab", { name: "Traces" }));
    await user.click(screen.getByText("book_ride"));
    expect(urlHasTrace()).toBe(true);
    await user.keyboard("{ArrowDown}");
    expect(onNext).not.toHaveBeenCalled();

    await user.click(screen.getByRole("tab", { name: "Transcript" }));
    expect(urlHasTrace()).toBe(false);
    await user.keyboard("{ArrowDown}");
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it("turns a voice call's prev and next off", async () => {
    const user = userEvent.setup();
    renderVoice({ hasPrev: true, hasNext: true });
    expect(screen.getByRole("button", { name: "next" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Traces" }));
    await user.click(screen.getByText("book_ride"));

    expect(screen.getByRole("button", { name: "next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "prev" })).toBeDisabled();
  });

  it("reads the trace with the run it was opened from", async () => {
    const user = userEvent.setup();
    renderChat();

    await user.click(screen.getByRole("tab", { name: "Traces" }));
    await user.click(screen.getByText("book_ride"));

    expect(useLLMTracingStore.getState().traceDetailDrawerOpen).toEqual({
      traceId: "trace-1",
      projectId: "hidden-project",
      testExecutionId: "run-1",
    });
  });
});

describe("CallDrawer — Escape", () => {
  it("closes the open trace and leaves the call drawer open", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderChat({ onClose });
    await user.click(screen.getByRole("tab", { name: "Traces" }));
    await user.click(screen.getByText("book_ride"));
    expect(screen.getByTestId("trace-drawer")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByTestId("trace-drawer")).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", { name: "Traces" })).toBeInTheDocument();
  });

  it("closes the call drawer when no trace is open", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderChat({ onClose });
    await user.click(screen.getByRole("tab", { name: "Traces" }));

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does the same on a voice call", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderVoice({ onClose });
    await user.click(screen.getByRole("button", { name: "Traces" }));
    await user.click(screen.getByText("book_ride"));

    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("trace-drawer")).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
