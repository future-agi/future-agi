// New experiment › Configure: the run kinds a door or the picker hands back
// follow the server's report (GET /api/capabilities/ experiment_run_sources).
import React from "react";
import { useForm } from "react-hook-form";
import { act, cleanup, render, screen } from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import {
  FAILED_AVAILABILITY,
  LOADING_AVAILABILITY,
  availabilityOfReport,
} from "src/sections/common/RunSources/runSourceAvailability";
import ConfigureStepLLM from "../StepperComponentExperiment/ConfigureStepLLM";

const harness = vi.hoisted(() => ({ availability: null, picker: null }));

vi.mock("src/sections/common/RunSources/useRunSourceAvailability", () => ({
  useRunSourceAvailability: () => harness.availability,
}));
// The picker hands kinds back through onAddCard; the door's anchor is its card.
vi.mock("src/sections/common/RunSources/SourcePicker", () => ({
  default: (props) => {
    harness.picker = props;
    return <div data-testid="source-card-agent" />;
  },
}));
/* eslint-disable react/prop-types */
vi.mock("../RunTargetPicker", () => ({
  default: ({ anchorEl, type }) =>
    anchorEl ? <div data-testid={`run-target-picker-${type}`} /> : null,
}));
/* eslint-enable react/prop-types */
vi.mock("../HttpRunForm", () => ({
  default: () => <div data-testid="http-run-form" />,
}));
vi.mock("../EndpointLimitsRow", () => ({ default: () => null }));
vi.mock("../StepperComponentExperiment/AgentPromptRenderer", () => ({
  default: () => <div data-testid="agent-prompt-renderer" />,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "rows", dataset: { kind: "rows" } }),
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunConfigs: () => ({ data: null }),
}));
vi.mock("../reservedIds", async (importOriginal) => ({
  ...(await importOriginal()),
  useReservedIds: () => ({ data: null }),
}));
vi.mock("src/api/develop/prompt", async (importOriginal) => ({
  ...(await importOriginal()),
  usePromptExecutions: () => ({ data: { pages: [] }, isPending: false }),
}));

const YES = { available: true, reason: null };
const NO = (reason) => ({ available: false, reason });
const AGENT_OFF =
  "Agent graph experiments are unavailable on this server. Contact your administrator.";
const ready = (sources = {}) =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: {
      prompt: YES,
      agent: YES,
      http: YES,
      a2a: NO("A2A experiments are not built in this release."),
      mcp_tool: NO("MCP tool experiments are not built in this release."),
      ...sources,
    },
  });

function Configure() {
  const form = useForm({ defaultValues: { promptConfig: [] } });
  return <ConfigureStepLLM {...form} />;
}

beforeEach(() => {
  // The enhanced Configure step (exp.new) in a non-launch test build.
  setLaunchOverrideForTest({ launch: false });
  harness.availability = ready();
  harness.picker = null;
  useRunExperimentStore.setState({ firstRunType: null, prefill: null });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setLaunchOverrideForTest(null);
  useRunExperimentStore.setState({ firstRunType: null, prefill: null });
});

describe("Configure adds only the run kinds the server can run", () => {
  it("adds the HTTP run form when the picker hands back an endpoint the server runs", () => {
    render(<Configure />);
    act(() => harness.picker.onAddCard("http"));
    expect(screen.getByTestId("http-run-form")).toBeInTheDocument();
    expect(screen.queryByTestId("source-card-agent")).toBeNull();
  });

  it.each([
    ["loading", () => LOADING_AVAILABILITY],
    ["failed", () => FAILED_AVAILABILITY],
    ["unavailable", () => ready({ http: NO("Not on this server.") })],
  ])("adds no HTTP run while the report is %s", (_, a) => {
    harness.availability = a();
    render(<Configure />);
    act(() => harness.picker.onAddCard("http"));
    expect(screen.queryByTestId("http-run-form")).toBeNull();
    // Step 1 stays, where the cards say what can run.
    expect(screen.getByTestId("source-card-agent")).toBeInTheDocument();
  });

  it.each(["a2a", "mcp_tool"])(
    "adds no %s run the server cannot run",
    (kind) => {
      render(<Configure />);
      act(() => harness.picker.onAddCard(kind));
      expect(screen.getByTestId("source-card-agent")).toBeInTheDocument();
      expect(screen.queryByText(/A2A agent card|MCP server tool/)).toBeNull();
    },
  );

  it("does not open the agent list from a door when the server says Agent graph cannot run here", () => {
    vi.useFakeTimers();
    harness.availability = ready({ agent: NO(AGENT_OFF) });
    useRunExperimentStore.setState({ firstRunType: "agent" });
    render(<Configure />);
    act(() => vi.advanceTimersByTime(100));
    expect(screen.queryByTestId("run-target-picker-agent")).toBeNull();
    expect(screen.getByTestId("source-card-agent")).toBeInTheDocument();
  });

  it.each([
    ["ready", () => ready()],
    ["loading", () => LOADING_AVAILABILITY],
  ])("opens the agent list from that door while the report is %s", (_, a) => {
    vi.useFakeTimers();
    harness.availability = a();
    useRunExperimentStore.setState({ firstRunType: "agent" });
    render(<Configure />);
    act(() => vi.advanceTimersByTime(100));
    expect(screen.getByTestId("run-target-picker-agent")).toBeInTheDocument();
  });
});
