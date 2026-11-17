import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LOADING_AVAILABILITY,
  availabilityOfReport,
} from "src/sections/common/RunSources/runSourceAvailability";
import NewExperimentButton from "../NewExperimentButton";

const renderButton = (props = {}) => render(<NewExperimentButton {...props} />);

const flags = vi.hoisted(() => ({
  launch: true,
  kinds: true,
  running: false,
  availability: null,
  reads: [],
}));
const open = vi.hoisted(() => vi.fn());
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isLaunchBuild: () => flags.launch,
  isSurfaceShown: (id) =>
    !flags.launch ||
    (id === "exp.new.kindMenu"
      ? flags.kinds
      : id === "exp.new.runningSources"
        ? flags.running
        : true),
}));
vi.mock("src/sections/common/RunSources/useRunSourceAvailability", () => ({
  useRunSourceAvailability: (options) => {
    flags.reads.push(options);
    return flags.availability;
  },
}));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ dataset: "ds" }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
// Inspect the exact title passed to the real tooltip, without jsdom hover timing.
/* eslint-disable react/prop-types */
vi.mock("src/components/tooltip", () => ({
  default: ({ children, title, show }) => (
    <div>
      {show ? <div data-testid="tooltip-copy">{title}</div> : null}
      {children}
    </div>
  ),
}));
/* eslint-enable react/prop-types */
// Single-export modules are replaced whole; partial mocks spread the original.
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "owner" }),
}));
vi.mock("src/utils/rolePermissionMapping", async (importOriginal) => ({
  ...(await importOriginal()),
  PERMISSIONS: { UPDATE: "update" },
  RolePermission: { DATASETS: { update: { owner: true } } },
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({ can: { run: true }, reason: () => null }),
}));
vi.mock("../../states", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunExperimentStoreShallow: (select) =>
    select({ initiateCreateMode: open }),
}));
vi.mock(
  "../../DataTab/Versions/PendingChangesGate",
  async (importOriginal) => ({
    ...(await importOriginal()),
    usePendingChangesGate: () => (_action, callback) => callback(),
  }),
);
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "rows", dataset: { kind: "rows" } }),
}));
vi.mock("../../ExperimentTab/useOpenSource", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => open,
}));

// The server's report (GET /api/capabilities/ experiment_run_sources).
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
const EVERY_SOURCE = () => ready({ a2a: YES, mcp_tool: YES });

beforeEach(() => {
  Object.assign(flags, {
    launch: true,
    kinds: true,
    running: false,
    availability: ready(),
    reads: [],
  });
  open.mockClear();
});
afterEach(cleanup);

const titles = () =>
  screen.getAllByTestId("tooltip-copy").map((el) => el.textContent);
const openKinds = () =>
  fireEvent.click(screen.getByTestId("new-experiment-kinds"));

describe("New experiment never advertises hidden running sources", () => {
  it.each(["header", "experiments"])(
    "names only prompts and agent graphs in the %s arrow",
    (placement) => {
      renderButton({ placement });
      expect(titles()).toContain(
        "Pick what to run: a prompt or an agent graph",
      );
      expect(titles().join(" ")).not.toMatch(
        /endpoint|A2A|MCP|already running/i,
      );
      openKinds();
      expect(
        screen.getByTestId("new-experiment-kind-prompt"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("new-experiment-kind-agent"),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("new-experiment-kind-running")).toBeNull();
      expect(screen.getAllByRole("menuitem")).toHaveLength(2);
    },
  );

  it("names the qualified agent path in the header hover", () => {
    renderButton();
    expect(titles()).toContain(
      "Run prompts and agent graphs on this dataset and compare them. Read more",
    );
  });

  it("keeps the shipped prompt-only header unchanged and has no arrow", () => {
    flags.kinds = false;
    renderButton();
    expect(titles()).toEqual([
      "Run prompts on this dataset and compare them. Read more",
    ]);
    expect(screen.queryByTestId("new-experiment-kinds")).toBeNull();
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New experiment" }));
    expect(open).toHaveBeenCalledWith({
      prefill: undefined,
      firstRunType: undefined,
    });
    // A build with neither the kinds nor Already running reads no report.
    expect(flags.reads.every((options) => options?.enabled === false)).toBe(
      true,
    );
  });

  it.each(["non-launch", "all-shown"])(
    "names every source in %s copy when the server can run them all",
    (mode) => {
      flags.launch = mode !== "non-launch";
      flags.running = mode === "all-shown";
      flags.availability = EVERY_SOURCE();
      renderButton();
      expect(titles()).toContain(
        "Run prompts, agent graphs, endpoints, A2A agents or MCP tools on this dataset and compare them. Read more",
      );
      expect(titles()).toContain(
        "Pick what to run: a prompt, an agent graph, or something already running (an endpoint, an A2A agent or an MCP tool)",
      );
    },
  );
});

describe("New experiment kinds follow the server's report", () => {
  beforeEach(() => {
    flags.running = true;
  });

  it("names only the endpoint when the server runs only that", () => {
    renderButton();
    expect(titles()).toContain(
      "Run prompts, agent graphs or endpoints on this dataset and compare them. Read more",
    );
    expect(titles()).toContain(
      "Pick what to run: a prompt, an agent graph, or something already running (an endpoint)",
    );
    expect(titles().join(" ")).not.toMatch(/A2A|MCP/);
    openKinds();
    const running = screen.getByTestId("new-experiment-kind-running");
    expect(
      within(running).getByText("An HTTP endpoint you host."),
    ).toBeInTheDocument();
    expect(screen.getByRole("menu")).not.toHaveTextContent(/A2A|MCP/);
    fireEvent.click(running);
    expect(open).toHaveBeenCalledExactlyOnceWith({ cardId: "running" });
  });

  it.each([
    ["loading", () => LOADING_AVAILABILITY],
    ["unavailable", () => ready({ http: NO("Not on this server.") })],
  ])(
    "lists Already running, saying why it cannot run, while the report is %s",
    (_, a) => {
      flags.availability = a();
      renderButton();
      // Copy names only what can be picked.
      expect(titles().join(" ")).not.toMatch(
        /endpoint|A2A|MCP|already running/i,
      );
      openKinds();
      // Prompt, Agent graph, Already running, then the three held ways
      // (newExperimentButton.everyWay.test.jsx).
      expect(screen.getAllByRole("menuitem")).toHaveLength(6);
      const running = screen.getByTestId("new-experiment-kind-running");
      expect(
        within(running).getByText("Not available in this deployment"),
      ).toBeInTheDocument();
      // It still opens its list, where every source shows greyed with why.
      expect(running).not.toHaveAttribute("aria-disabled");
      fireEvent.click(running);
      expect(open).toHaveBeenCalledExactlyOnceWith({ cardId: "running" });
    },
  );

  it("lists Agent graph with the server's reason and does not open it", () => {
    flags.availability = ready({ agent: NO(AGENT_OFF) });
    renderButton();
    openKinds();
    const agent = screen.getByTestId("new-experiment-kind-agent");
    expect(agent).toHaveAttribute("aria-disabled", "true");
    expect(agent).toHaveClass("Mui-disabled");
    expect(within(agent).getByText(AGENT_OFF)).toBeInTheDocument();
    expect(agent).not.toHaveTextContent(
      "A graph you built in Agent Playground.",
    );
    // The refused kind stays in the arrow-key order, so its reason is read.
    const prompt = screen.getByTestId("new-experiment-kind-prompt");
    act(() => prompt.focus());
    fireEvent.keyDown(prompt, { key: "ArrowDown" });
    expect(agent).toHaveFocus();
    fireEvent.keyDown(agent, { key: "Enter" });
    fireEvent.click(agent);
    expect(open).not.toHaveBeenCalled();
    fireEvent.keyDown(agent, { key: "ArrowDown" });
    expect(screen.getByTestId("new-experiment-kind-running")).toHaveFocus();
    fireEvent.click(prompt);
    expect(open).toHaveBeenCalledExactlyOnceWith({ cardId: "prompt" });
  });

  it("points the hover and the arrow away from a refused Agent graph", () => {
    flags.availability = ready({ agent: NO(AGENT_OFF) });
    renderButton();
    expect(titles()).toContain(
      "Run prompts or endpoints on this dataset and compare them. Read more",
    );
    expect(titles()).toContain(
      "Pick what to run: a prompt or something already running (an endpoint)",
    );
    expect(titles().join(" ")).not.toMatch(/agent graph/i);
  });

  it("names only prompts when the server refuses Agent graph and runs nothing already running", () => {
    flags.availability = ready({
      agent: NO(AGENT_OFF),
      http: NO("Not on this server."),
    });
    renderButton();
    expect(titles()).toContain(
      "Run prompts on this dataset and compare them. Read more",
    );
    expect(titles()).toContain("Pick what to run: a prompt");
  });

  it("reads the report while the kinds menu or Already running shows", () => {
    renderButton();
    expect(flags.reads.length).toBeGreaterThan(0);
    expect(flags.reads.every((options) => options?.enabled === true)).toBe(
      true,
    );
  });
});
