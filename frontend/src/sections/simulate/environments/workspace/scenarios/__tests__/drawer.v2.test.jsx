import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import {
  renderWithRouter,
  screen,
  fireEvent,
  within,
  waitFor,
  act,
} from "src/utils/test-utils";
import { scenarioFromApi } from "src/api/simulate-environments/scenarios";
import { useCallExecutionV3Detail } from "src/api/simulate-environments/runDetail";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import ScenarioDrawer from "../ScenarioDrawer";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallExecutionV3Detail: vi.fn(),
}));

// The run's own call drawer is reused as is; here it only reports what it got.
vi.mock("../../runs/detail/CallDrawer", () => {
  const CallDrawerStub = ({ task, executionId }) =>
    task ? (
      <div data-testid="call-drawer">
        {`${task.id}|${task.simulationCallType}|${executionId}`}
      </div>
    ) : null;
  CallDrawerStub.propTypes = {
    task: PropTypes.object,
    executionId: PropTypes.string,
  };
  return { default: CallDrawerStub };
});

const API_ROW = {
  id: "s1",
  scenario_key: "pricing_objection_handled",
  number: 3,
  name: "pricing_objection_handled",
  use_case: "Handle customer objections during sales inquiries.",
  instruction:
    "You run a small 5-person team.\nAsk why the plan costs more than a rival.",
  tests: "the agent explains the price and offers the cheaper plan",
  branch: "objection",
  status: "authored",
  call_execution_id: "ce-42",
  persona: { name: "Marcus Chen", gender: "male", age_group: "40-50" },
  sub_goals: ["acknowledges_concern", "offers_alternative"],
};

const open = ({ apiRow = API_ROW, route = "/x", ...props } = {}) => {
  const handlers = { onClose: vi.fn(), onEdit: vi.fn(), onRemove: vi.fn() };
  const row = scenarioFromApi(apiRow);
  const utils = renderWithRouter(
    <ScenarioDrawer row={row} {...handlers} {...props} />,
    { route },
  );
  return { ...utils, ...handlers, row };
};

beforeEach(() => {
  vi.mocked(enqueueSnackbar).mockClear();
  vi.mocked(useCallExecutionV3Detail).mockReset();
  vi.mocked(useCallExecutionV3Detail).mockReturnValue({
    data: undefined,
    isPending: true,
    isError: false,
  });
});

describe("ScenarioDrawer header", () => {
  it("leads with the scenario key, flags State and Source as coming soon", () => {
    open();
    expect(
      screen.getAllByText("pricing_objection_handled").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("State soon")).toBeInTheDocument();
    expect(screen.getByText("Source soon")).toBeInTheDocument();
  });

  it("toggles must-pass locally only, flagged UI only", () => {
    open();
    const toggle = screen.getByRole("button", { name: "Must-pass" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    // The flag sits beside the toggle, outside its accessible name.
    const flagGroup = toggle.parentElement.parentElement;
    expect(within(flagGroup).getByLabelText("UI only")).toBeInTheDocument();
  });

  it("locks must-pass and the menu when the scenarios are locked", () => {
    open({ locked: true });
    expect(screen.getByRole("button", { name: "Must-pass" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "More actions" })).toBeDisabled();
  });

  it("offers Edit, Archive and Remove from the more-actions menu", () => {
    const { onEdit, onRemove } = open();
    const openMenu = () =>
      fireEvent.click(screen.getByRole("button", { name: "More actions" }));

    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Edit/ }));
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ id: "s1" }));

    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Archive/ }));
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    expect(onRemove).not.toHaveBeenCalled();

    openMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /Remove/ }));
    expect(onRemove).toHaveBeenCalledWith("s1");
  });
});

describe("ScenarioDrawer latest run", () => {
  it("shows the status and opens the conversation in the run's call drawer", async () => {
    vi.mocked(useCallExecutionV3Detail).mockReturnValue({
      data: { simulation_call_type: "voice" },
      isPending: false,
      isError: false,
    });
    open();
    expect(screen.getByText("Latest run")).toBeInTheDocument();
    expect(screen.getByText("Authored")).toBeInTheDocument();
    expect(screen.queryByTestId("call-drawer")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Open conversation/ }));
    expect(
      await screen.findByTestId("call-drawer", {}, { timeout: 5000 }),
    ).toHaveTextContent("ce-42|voice|null");
    expect(useCallExecutionV3Detail).toHaveBeenLastCalledWith("ce-42", true);
  });

  it("waits for the call's type before opening its drawer", async () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: /Open conversation/ }));
    // The lazy call drawer has loaded; it is the pending type that holds it shut.
    await act(() => vi.dynamicImportSettled());
    expect(screen.queryByTestId("call-drawer")).toBeNull();
  });

  it("says No run yet when the scenario has no call", () => {
    open({ apiRow: { ...API_ROW, call_execution_id: null } });
    expect(screen.getByText("No run yet")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Open conversation/ }),
    ).toBeNull();
  });
});

describe("ScenarioDrawer figures and sheet", () => {
  it("counts the real sub-goals; runs, checks and changes are coming soon", () => {
    open();
    const figure = screen.getByTestId("figure-sub-goals");
    expect(within(figure).getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Runs soon")).toBeInTheDocument();
    expect(screen.getByText("Checks soon")).toBeInTheDocument();
    expect(screen.getByText("Changes soon")).toBeInTheDocument();
  });

  it("lays the real fields out in Vel's order", () => {
    open();
    const labels = [
      "#",
      "Passes when",
      "Caller",
      "Brief",
      "Sub-goals",
      "Path",
    ].map((l) => screen.getAllByText(l).at(-1));
    labels
      .slice(1)
      .forEach((el, i) =>
        expect(
          labels[i].compareDocumentPosition(el) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBeTruthy(),
      );
    expect(
      screen.getByText(
        "the agent explains the price and offers the cheaper plan",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Marcus Chen")).toBeInTheDocument();
    expect(screen.getByText("objection")).toBeInTheDocument();
    expect(screen.getByText("Graded on")).toBeInTheDocument();
    expect(screen.getByText("Tools it needs")).toBeInTheDocument();
  });
});

describe("ScenarioDrawer proof and internals", () => {
  it("shows both open, saying they aren't in the API yet, with no sample content", () => {
    open();
    expect(screen.getAllByText("Not in the API yet")).toHaveLength(2);
    expect(screen.getByText("Proof soon")).toBeInTheDocument();
    expect(screen.getByText("Internals soon")).toBeInTheDocument();
    expect(screen.queryByText("Mutation controls")).toBeNull();
    expect(screen.queryByText(/reference solution/i)).toBeNull();
  });

  it("collapses and reopens each section", async () => {
    open();
    const toggle = screen.getByRole("button", { name: /Proof details/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await waitFor(() =>
      expect(screen.getAllByText("Not in the API yet")).toHaveLength(1),
    );
    fireEvent.click(screen.getByRole("button", { name: /Internals details/ }));
    await waitFor(() =>
      expect(screen.queryByText("Not in the API yet")).toBeNull(),
    );
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Not in the API yet")).toBeInTheDocument();
  });

  it("fills with Vel's sample, flagged UI only, under ?uiSample=proof", () => {
    open({ route: "/x?uiSample=proof" });
    expect(screen.queryByText("Not in the API yet")).toBeNull();
    expect(screen.queryByText("Proof soon")).toBeNull();
    expect(screen.getAllByText("Mutation controls").length).toBeGreaterThan(0);
    expect(screen.getByText(/The simulated caller/)).toBeInTheDocument();
    expect(screen.getByText(/The reference solution/)).toBeInTheDocument();
    expect(screen.getAllByLabelText("UI only").length).toBeGreaterThanOrEqual(
      3,
    );
  });
});
