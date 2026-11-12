// The New experiment ▾ menu lists the ways step 1
// lists, in its order. Prompt, Agent graph and Already running work as
// before. Your code (SDK / CLI), Agent harness and Voice agent are listed
// greyed with "Not available in this release" and open nothing by click or
// keyboard.
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
  FAILED_AVAILABILITY,
  LOADING_AVAILABILITY,
  availabilityOfReport,
} from "src/sections/common/RunSources/runSourceAvailability";
import { stepCardsFor } from "src/sections/common/RunSources/catalog";
import NewExperimentButton from "../NewExperimentButton";

const flags = vi.hoisted(() => ({ running: true, availability: null }));
const open = vi.hoisted(() => vi.fn());
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isLaunchBuild: () => true,
  isSurfaceShown: (id) =>
    id === "exp.new.runningSources" ? flags.running : true,
}));
vi.mock("src/sections/common/RunSources/useRunSourceAvailability", () => ({
  useRunSourceAvailability: () => flags.availability,
}));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ dataset: "ds" }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
// Inspect the exact title passed to the tooltip, without jsdom hover timing.
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

// What the cut-3 server reports (GET /api/capabilities/
// experiment_run_sources): HTTP runs, A2A and MCP tool are not built.
const YES = { available: true, reason: null };
const NO = (reason) => ({ available: false, reason });
const READY = () =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: {
      prompt: YES,
      agent: YES,
      http: YES,
      a2a: NO("A2A experiments are not built in this release."),
      mcp_tool: NO("MCP tool experiments are not built in this release."),
    },
  });

const LINE = "Not available in this release";
// [id, title]
const HELD = [
  ["code", "Your code (SDK / CLI)"],
  ["harness", "Agent harness"],
  ["voice", "Voice agent"],
];
// [id, title, line] with the cut-3 report.
const WORKING = [
  ["prompt", "Prompt", "A prompt version and a model, with tools."],
  ["agent", "Agent graph", "A graph you built in Agent Playground."],
  ["running", "Already running", "An HTTP endpoint you host."],
];
const SIX = ["prompt", "agent", "running", "code", "harness", "voice"];

beforeEach(() => {
  flags.running = true;
  flags.availability = READY();
  open.mockClear();
});
afterEach(cleanup);

const titles = () =>
  screen.getAllByTestId("tooltip-copy").map((el) => el.textContent);
const openKinds = () =>
  fireEvent.click(screen.getByTestId("new-experiment-kinds"));
const kindItem = (id) => screen.getByTestId(`new-experiment-kind-${id}`);
const idsOf = (items) =>
  items.map((el) =>
    el.getAttribute("data-testid").replace("new-experiment-kind-", ""),
  );
const menuIds = () => idsOf(screen.getAllByRole("menuitem"));
const stepOneIds = () =>
  stepCardsFor("rows", null, flags.availability).cards.map((c) => c.id);

describe("the New experiment ▾ menu lists every way step 1 lists", () => {
  it.each(["header", "experiments"])(
    "the %s button lists six ways in step 1's order, the three held ones greyed with the release line",
    (placement) => {
      render(<NewExperimentButton placement={placement} />);
      openKinds();
      const menu = screen.getByRole("menu", { name: "What to run" });
      const items = within(menu).getAllByRole("menuitem");
      expect(items).toHaveLength(6);
      expect(idsOf(items)).toEqual(SIX);
      // The same ways, in the same order, as step 1 (SourceCards).
      expect(idsOf(items)).toEqual(stepOneIds());
      HELD.forEach(([id, title]) => {
        const item = kindItem(id);
        expect(within(item).getByText(title)).toBeInTheDocument();
        expect(within(item).getByText(LINE)).toBeInTheDocument();
        expect(item).toHaveAttribute("aria-disabled", "true");
        expect(item).toHaveClass("Mui-disabled");
        // A screen reader hears what it is and why it cannot be picked.
        expect(item).toHaveAccessibleName(expect.stringContaining(title));
        expect(item).toHaveAccessibleName(expect.stringContaining(LINE));
        expect(
          within(item).queryByTestId("new-experiment-recommended-tag"),
        ).toBeNull();
      });
      expect(within(menu).getAllByText(LINE)).toHaveLength(3);
      WORKING.forEach(([id, title, line]) => {
        const item = kindItem(id);
        expect(item).not.toHaveAttribute("aria-disabled");
        expect(within(item).getByText(title)).toBeInTheDocument();
        expect(within(item).getByText(line)).toBeInTheDocument();
      });
      // Recommended stays on Prompt, the first card for rows.
      expect(
        within(kindItem("prompt")).getByTestId(
          "new-experiment-recommended-tag",
        ),
      ).toBeInTheDocument();
      expect(
        screen.getAllByTestId("new-experiment-recommended-tag"),
      ).toHaveLength(1);
    },
  );

  it.each([
    ["loading", () => LOADING_AVAILABILITY],
    ["failed", () => FAILED_AVAILABILITY],
  ])(
    "lists the held ways greyed while the report is %s, and Already running still opens its list",
    (_, a) => {
      flags.availability = a();
      render(<NewExperimentButton />);
      openKinds();
      expect(menuIds()).toEqual(SIX);
      HELD.forEach(([id]) => {
        expect(kindItem(id)).toHaveAttribute("aria-disabled", "true");
        expect(within(kindItem(id)).getByText(LINE)).toBeInTheDocument();
      });
      const running = kindItem("running");
      expect(
        within(running).getByText("Not available in this deployment"),
      ).toBeInTheDocument();
      expect(running).not.toHaveAttribute("aria-disabled");
      fireEvent.click(running);
      expect(open).toHaveBeenCalledExactlyOnceWith({ cardId: "running" });
    },
  );

  it.each(HELD)("%s opens nothing by click, Enter or Space", (id) => {
    render(<NewExperimentButton />);
    openKinds();
    const item = kindItem(id);
    fireEvent.click(item);
    fireEvent.keyDown(item, { key: "Enter" });
    fireEvent.keyDown(item, { key: " " });
    fireEvent.keyUp(item, { key: " " });
    expect(open).not.toHaveBeenCalled();
    // Nothing was picked, so the menu stays open.
    expect(screen.getByRole("menu", { name: "What to run" })).toBeVisible();
  });

  it("keeps the held ways in the arrow-key order, so their reason is read, and Enter on them opens nothing", () => {
    render(<NewExperimentButton />);
    openKinds();
    act(() => kindItem("running").focus());
    HELD.forEach(([id]) => {
      fireEvent.keyDown(document.activeElement, { key: "ArrowDown" });
      expect(kindItem(id)).toHaveFocus();
      fireEvent.keyDown(kindItem(id), { key: "Enter" });
    });
    expect(open).not.toHaveBeenCalled();
  });

  it.each(WORKING)(
    "%s opens as before, next to the held ways",
    (id, title, line) => {
      render(<NewExperimentButton />);
      openKinds();
      expect(menuIds()).toEqual(SIX);
      const item = kindItem(id);
      expect(item).not.toHaveAttribute("aria-disabled");
      expect(item).not.toHaveClass("Mui-disabled");
      expect(within(item).getByText(title)).toBeInTheDocument();
      expect(within(item).getByText(line)).toBeInTheDocument();
      expect(item).not.toHaveTextContent(LINE);
      fireEvent.click(item);
      expect(open).toHaveBeenCalledExactlyOnceWith({ cardId: id });
    },
  );

  // Guard (passes on BASE too): the held ways follow Already running, as on
  // step 1, so a build that hides it lists Prompt and Agent graph only.
  it("lists the held ways only where Already running shows, as step 1 does", () => {
    flags.running = false;
    render(<NewExperimentButton />);
    openKinds();
    expect(menuIds()).toEqual(["prompt", "agent"]);
    expect(stepOneIds()).toEqual(["prompt", "agent"]);
  });
});

// Guard (passes on BASE too): the hover and arrow copy name only what can
// be picked here, never A2A, MCP or a held way.
describe("the New experiment hover and arrow copy promise only what runs", () => {
  it.each([
    [
      "the cut-3 report",
      READY,
      "Run prompts, agent graphs or endpoints on this dataset and compare them. Read more",
      "Pick what to run: a prompt, an agent graph, or something already running (an endpoint)",
    ],
    [
      "a loading report",
      () => LOADING_AVAILABILITY,
      "Run prompts and agent graphs on this dataset and compare them. Read more",
      "Pick what to run: a prompt or an agent graph",
    ],
    [
      "a failed read",
      () => FAILED_AVAILABILITY,
      "Run prompts and agent graphs on this dataset and compare them. Read more",
      "Pick what to run: a prompt or an agent graph",
    ],
  ])("with %s", (_, a, hover, arrow) => {
    flags.availability = a();
    render(<NewExperimentButton />);
    const copy = titles();
    expect(copy).toEqual([hover, arrow]);
    expect(copy.join(" ")).not.toMatch(/A2A|MCP|SDK|CLI|harness|voice/i);
  });
});
