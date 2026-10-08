/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useState } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// The run page's eval actions end to end: the real All Evaluations drawer, the
// real column menu, the real confirm and the real re-run request, so the two
// ways in can be seen sharing (or not sharing) one request.

const useRunDetail = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunDetail: (...args) => useRunDetail(...args),
  useOptimizationRuns: () => ({ runs: [], isLoading: false }),
}));

// Left hanging, so the page can be looked at while the re-run is on its way.
const runEvaluationsAgain = vi.fn();
vi.mock(
  "src/api/simulate-environments/harnessEnvironments",
  async (importOriginal) => ({
    ...(await importOriginal()),
    runEvaluationsAgain: (...args) => runEvaluationsAgain(...args),
  }),
);

const CONFIGS = [
  {
    id: "c1",
    name: "no_misselling",
    mapping: { conversation: "voice_recording" },
    eval_type: "llm",
    regradable: true,
    editable: true,
  },
];
vi.mock(
  "src/api/simulate-environments/environments",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useEnvironmentRunTest: () => ({
      data: CONFIGS,
      isPending: false,
      isError: false,
    }),
    useRemoveAppliedEvaluation: () => ({ mutate: vi.fn(), isPending: false }),
  }),
);

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

// The edit form, as a real modal drawer, so its stacking against the All
// Evaluations drawer is the real one. Its own tests cover the form.
vi.mock("../../../evals/AddEvaluationDrawer", async () => {
  const { default: SideDrawer } = await import(
    "../../../../components/SideDrawer"
  );
  return {
    default: ({ open, editingEval, onClose, onEdited }) =>
      editingEval ? (
        <SideDrawer open={open} onClose={onClose}>
          <div>{`edit-drawer:${editingEval.id}`}</div>
          <button type="button" onClick={() => onEdited(editingEval)}>
            save edit
          </button>
        </SideDrawer>
      ) : null,
  };
});

// The table mounts the menu exactly as RunTraceTable does; one button stands
// in for the column header's ⋮.
vi.mock("../trace/RunTraceTable", async () => {
  const { default: EvalColumnActions } = await import("../EvalColumnActions");
  return {
    default: function RunTraceTableStub({ evalActions }) {
      const [menuFor, setMenuFor] = useState(null);
      if (!evalActions) return null;
      return (
        <>
          <button
            type="button"
            onClick={(event) =>
              setMenuFor({
                evalId: "c1",
                name: "no_misselling",
                anchorEl: event.currentTarget,
              })
            }
          >
            Actions for no_misselling
          </button>
          <EvalColumnActions
            {...evalActions}
            menuFor={menuFor}
            onClose={() => setMenuFor(null)}
          />
        </>
      );
    },
  };
});

vi.mock("../CallDrawer", () => ({ default: () => null }));
vi.mock("../RunAnalytics", () => ({ default: () => null }));
vi.mock("../StopRunControl", () => ({ default: () => null }));
vi.mock("../fixmyagent/FixMyAgentDrawer", () => ({ default: () => null }));
vi.mock("../fixmyagent/LaunchOptimizationDrawer", () => ({
  default: () => null,
}));
vi.mock("../fixmyagent/selfImprovement", async (importOriginal) => ({
  ...(await importOriginal()),
  useSelfImprovementOpen: () => true,
}));
vi.mock("../useCallListNavigation", () => ({
  default: () => ({ hasPrev: false, hasNext: false }),
}));
vi.mock("../useOpenCallParam", () => ({
  default: () => ({ openCall: null, showCall: vi.fn() }),
}));

const { default: RunDetail } = await import("../RunDetail");

const IDENTITY = {
  id: "ex1",
  executionId: "ex1",
  ordinal: 3,
  letter: "3",
  color: "#7857FC",
  agentVersion: "v2",
  status: "passed",
  runState: "finished",
  executionStatus: "completed",
};

beforeEach(() => {
  runEvaluationsAgain.mockReset();
  runEvaluationsAgain.mockReturnValue(new Promise(() => {}));
  useRunDetail.mockReturnValue({
    identity: IDENTITY,
    stats: { total: 4, agentType: "voice" },
    isLoading: false,
  });
});

const renderDetail = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <RunDetail
          env={{ id: "env-1", name: "Refund Copilot", platform: {} }}
          envState={{ evals: [] }}
          backed
          testId="rt1"
          executionId="ex1"
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );

// Outlast a modal's exit transition.
const settle = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 500)));

// Send a re-run from whichever confirm is open, then step back out of the
// confirm while the request is still on its way.
const confirmAndLeave = async (user) => {
  await user.click(screen.getByRole("button", { name: "Run Evaluations" }));
  expect(runEvaluationsAgain).toHaveBeenCalledWith("env-1", "ex1", ["c1"]);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await settle();
};

const menuItem = (label) =>
  screen.getByRole("menuitem", { name: new RegExp(`^${label}`) });

describe("RunDetail — the drawer and the column menus share one re-run", () => {
  it("holds a column's Re-run while one sent from the drawer is on its way", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Run evals" }));
    await user.click(screen.getByRole("button", { name: "Run no_misselling" }));
    await confirmAndLeave(user);
    await user.click(screen.getByRole("button", { name: "Close" }));
    await settle();

    await user.click(
      screen.getByRole("button", { name: "Actions for no_misselling" }),
    );
    expect(menuItem("Re-run")).toHaveAttribute("aria-disabled", "true");
    expect(menuItem("Edit")).toHaveAttribute("aria-disabled", "true");
    expect(runEvaluationsAgain).toHaveBeenCalledTimes(1);
  });

  it("holds the drawer's runs while one sent from a column is on its way", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(
      screen.getByRole("button", { name: "Actions for no_misselling" }),
    );
    await user.click(menuItem("Re-run"));
    await confirmAndLeave(user);

    await user.click(screen.getByRole("button", { name: "Run evals" }));
    expect(
      screen.getByRole("button", { name: "Run no_misselling" }),
    ).toBeDisabled();
    expect(runEvaluationsAgain).toHaveBeenCalledTimes(1);
  });
});

describe("RunDetail — editing from a column menu", () => {
  it("opens the edit form on that column's eval, and saving it only saves", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(
      screen.getByRole("button", { name: "Actions for no_misselling" }),
    );
    await user.click(menuItem("Edit"));
    expect(await screen.findByText("edit-drawer:c1")).toBeInTheDocument();
    expect(
      screen.queryByText("This will overwrite previous evaluation results."),
    ).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(screen.getByRole("button", { name: "save edit" }));
    await waitFor(() =>
      expect(screen.queryByText("edit-drawer:c1")).toBeNull(),
    );
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.queryByText("This will overwrite previous evaluation results."),
    ).toBeNull();
    expect(runEvaluationsAgain).not.toHaveBeenCalled();
  });
});

describe("RunDetail — dialogs opened from the All Evaluations drawer", () => {
  // Every modal here sits at MUI's own z-index for its kind (drawers 1200,
  // dialogs 1300), so between two drawers the later one in the page is on
  // top, and MUI hides everything under the topmost from assistive tech.
  const modalOf = (node) => node.closest(".MuiModal-root");

  it("opens the edit form above the drawer, and saving it leaves the drawer open", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Run evals" }));
    const drawer = modalOf(screen.getByText("All Evaluations"));
    await user.click(
      screen.getByRole("button", { name: "Edit no_misselling" }),
    );

    const edit = modalOf(screen.getByText("edit-drawer:c1"));
    expect(edit).not.toBe(drawer);
    expect(
      drawer.compareDocumentPosition(edit) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(drawer).toHaveAttribute("aria-hidden", "true");
    expect(edit).not.toHaveAttribute("aria-hidden");

    await user.click(within(edit).getByRole("button", { name: "save edit" }));
    await waitFor(() =>
      expect(screen.queryByText("edit-drawer:c1")).toBeNull(),
    );
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(runEvaluationsAgain).not.toHaveBeenCalled();
    expect(drawer).toBeInTheDocument();
    expect(modalOf(screen.getByText("All Evaluations"))).toBe(drawer);
    expect(drawer).not.toHaveAttribute("aria-hidden");
  });

  it("opens a row's confirm above the drawer", async () => {
    const user = userEvent.setup();
    renderDetail();

    await user.click(screen.getByRole("button", { name: "Run evals" }));
    const drawer = modalOf(screen.getByText("All Evaluations"));
    await user.click(screen.getByRole("button", { name: "Run no_misselling" }));

    const confirm = screen.getByRole("dialog");
    expect(
      drawer.compareDocumentPosition(modalOf(confirm)) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(drawer).toHaveAttribute("aria-hidden", "true");
  });
});
