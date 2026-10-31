import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useState } from "react";
import {
  render as rtlRender,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import EvalsStep from "../EvalsStep";
import { EVALS_COPY } from "../evals.constants";
import { NO_MISSELLING, selectedEntry } from "./fixtures/evalEntries";

// The API client, mocked so a backed-env remove exercises the real DELETE path
// without the apiPath contract throw.
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: vi.fn(),
  deleteHarnessEnvironment: vi.fn(),
  renameHarnessEnvironment: vi.fn(),
  getHarnessEnvironment: vi.fn(),
  deleteAppliedEvaluation: vi.fn(() => Promise.resolve()),
  addRunEvaluation: vi.fn(),
  setToolCallEvaluation: vi.fn(),
  updateAppliedEvaluation: vi.fn(),
}));
const {
  deleteAppliedEvaluation,
  getHarnessEnvironment,
  setToolCallEvaluation,
  updateAppliedEvaluation,
  addRunEvaluation,
} = await import("src/api/simulate-environments/harnessEnvironments");

// Editing reads the run test's full eval rows (the tab's own rows are display
// entries); only that read goes through the shared client.
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), patch: vi.fn() },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
const { enqueueSnackbar } = await import("notistack");
const axios = (await import("src/utils/axios")).default;

// EvalsStep now uses a react-query mutation (remove-eval), so every render
// needs a client. Wrap the library render once so the call sites stay unchanged.
const render = (ui, options) =>
  rtlRender(
    <BrowserRouter>
      <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
    </BrowserRouter>,
    options,
  );

// The product eval picker is a large real drawer with its own data fetching;
// stub it with a marker that exposes the callbacks the wrapper wires up.
const picker = vi.hoisted(() => ({ calls: [] }));
vi.mock("src/sections/common/EvalPicker", async () => ({
  serializeEvalConfig: (
    await vi.importActual("src/sections/common/EvalPicker/serializeEvalConfig")
  ).serializeEvalConfig,
  EvalPickerDrawer: (p) => {
    picker.calls.push(p);
    return p.open ? (
      <div data-testid="eval-picker">
        <button
          type="button"
          onClick={() =>
            p.onEvalAdded({ templateId: "a", name: "A", mapping: {} })
          }
        >
          save-a
        </button>
        <button
          type="button"
          onClick={() =>
            p.onEvalAdded({ templateId: "b", name: "B", mapping: {} })
          }
        >
          save-b
        </button>
        <button type="button" onClick={p.onClose}>
          picker-close
        </button>
      </div>
    ) : null;
  },
}));

const ENV = {
  id: "env-1",
  name: "Support",
  surface: "voice",
  evalPreset: ["task_success", "policy_adherence", "pii_leakage"],
};

// eslint-disable-next-line react/prop-types
function Harness({ env = ENV, initial, onGo, patchSpy, ...flags }) {
  const [envState, setEnvState] = useState(initial);
  const patch = (p) => {
    patchSpy?.(p);
    setEnvState((s) => ({ ...s, ...p }));
  };
  return (
    <EvalsStep
      env={env}
      envState={envState}
      patch={patch}
      onGo={onGo}
      {...flags}
    />
  );
}

const detailWith = (selected) => ({ evaluations: { selected } });
const state = { scenarios: [{ id: "s1" }, { id: "s2" }], evals: [] };
const renderBacked = (route = "/x") => {
  window.history.pushState({}, "", route);
  return render(<Harness backed initial={state} patchSpy={vi.fn()} />);
};

beforeEach(() => {
  deleteAppliedEvaluation.mockClear();
  enqueueSnackbar.mockClear();
  getHarnessEnvironment.mockResolvedValue(
    detailWith([selectedEntry(NO_MISSELLING, "cfg-1")]),
  );
});

describe("Evaluations list in Vel's layout", () => {
  it('remove asks "Remove X? New runs won\'t be graded on it. Past scores stay." and removes only on confirm', async () => {
    renderBacked();
    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    fireEvent.click(
      screen.getAllByRole("button", { name: EVALS_COPY.remove })[0],
    );
    expect(screen.getByText("Remove no_misselling?")).toBeInTheDocument();
    expect(
      screen.getByText("New runs won't be graded on it. Past scores stay."),
    ).toBeInTheDocument();
    expect(deleteAppliedEvaluation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteAppliedEvaluation).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getAllByRole("button", { name: EVALS_COPY.remove })[0],
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove evaluation" }));
    await waitFor(() =>
      expect(deleteAppliedEvaluation).toHaveBeenCalledWith("env-1", "cfg-1"),
    );
  });

  it("Required switch is flagged UI only and sends nothing", async () => {
    renderBacked();
    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    const sw = screen.getByRole("checkbox", {
      name: "Required: no_misselling",
    });
    fireEvent.click(sw);
    expect(sw).toBeChecked();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "UI only. Nothing was saved.",
      { variant: "info" },
    );
    expect(screen.getAllByLabelText("UI only").length).toBeGreaterThan(0);
    expect(updateAppliedEvaluation).not.toHaveBeenCalled();
  });

  it("threshold is a soon chip", async () => {
    renderBacked();
    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    expect(screen.getByText("Threshold soon")).toBeInTheDocument();
  });

  it("keeps costs hidden, as the product decided; ?uiSample=evalCost shows Vel's cost line flagged", async () => {
    renderBacked();
    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    expect(screen.queryByText(/credits per/)).toBeNull();
    expect(screen.queryByText(/cost about/)).toBeNull();
  });

  it("the sample cost line reads Vel's sentence", async () => {
    renderBacked("/x?uiSample=evalCost");
    expect(await screen.findByText("no_misselling")).toBeInTheDocument();
    expect(
      screen.getByText(
        /This environment's evals cost about 3 credits per run \(2 scenarios × 3 repeats\)/,
      ),
    ).toBeInTheDocument();
  });
});
