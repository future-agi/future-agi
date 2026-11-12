// One run's own Stop and Resume on a server without run control. They name
// the run (selected-run control), which that server refuses with 404
// "run_control_not_enabled" before doing anything. They must never fall back
// to the empty-body stop: that cancels every run of the experiment, which is
// the experiment page's Stop to do. Before the page knows, one click may get
// the refusal; it says the reason, never a red error. Once the page knows,
// every door (the leaderboard row menu, the leaderboard status chip, the
// results run chip) shows the action disabled with the reason and sends
// nothing. The request functions are replaced by a stand-in server that
// records each request as the API client would send it.
/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import {
  noteRunControl,
  resetRunControlForTest,
  runControlState,
} from "src/sections/common/ExperimentRunControl/runControlAvailability";
import RunRowMenu from "src/sections/develop-detail/ExperimentTab/RunRowMenu";
import RunStatusCell from "src/sections/develop-detail/ExperimentTab/RunStatusCell";
import RunBar from "src/sections/experiment-detail/results/RunBar";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const RUN = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const STOP_HELD = `${NOT_IN_RELEASE}. Stop the whole experiment from its page.`;

const server = vi.hoisted(() => ({
  runControl: false,
  requests: [],
  toasts: [],
  notEnabled() {
    return {
      status: false,
      type: "not_found",
      code: "run_control_not_enabled",
      detail: "Run control is not available.",
      message: "Run control is not available.",
      statusCode: 404,
    };
  },
}));

vi.mock("src/api/develop/experiment-run-actions", async (importOriginal) => ({
  ...(await importOriginal()),
  stopRuns: async (experimentId, runIds) => {
    const body = runIds === undefined ? {} : { run_ids: runIds };
    server.requests.push({
      method: "post",
      url: `/model-hub/experiments/v2/${experimentId}/stop/`,
      body,
    });
    if (body.run_ids && !server.runControl) throw server.notEnabled();
    return {
      status: body.run_ids ? 202 : 200,
      data: {
        status: true,
        result: {
          experiment_id: experimentId,
          runs: (runIds ?? []).map((id) => ({ run_id: id, done: 4 })),
        },
      },
    };
  },
  resumeRuns: async (experimentId, runIds) => {
    server.requests.push({
      method: "post",
      url: `/model-hub/experiments/v2/${experimentId}/resume/`,
      body: { run_ids: runIds },
    });
    if (!server.runControl) throw server.notEnabled();
    return {
      status: 202,
      data: {
        status: true,
        result: {
          experiment_id: experimentId,
          runs: runIds.map((id) => ({ run_id: id, pending: 6 })),
        },
      },
    };
  },
}));
vi.mock("src/sections/common/ux/toast", async (importOriginal) => {
  const notify = (message, options = {}) => {
    server.toasts.push({ message, options });
    return { undone: Promise.resolve(false) };
  };
  return { ...(await importOriginal()), default: notify, notify };
});
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunConfigs: () => ({ data: { results: [] } }),
}));
vi.mock("src/sections/experiment-detail/results/RunDetailsPopover", () => ({
  default: () => null,
}));
vi.mock("src/sections/develop-detail/ExperimentTab/cellContext", () => ({
  useCell: (props) => props,
}));

const run = (status) => ({
  run_id: RUN,
  experiment_id: EXP,
  experiment_ordinal: 1,
  experiment_name: "Ticket triage",
  label: "#1A",
  name: "Prompt",
  target_type: "external",
  status,
  progress: { total: 10, done: 4, errors: 0 },
});

const context = {
  datasetId: "ds",
  runsPerExperiment: { [EXP]: 2 },
  canEdit: true,
  canRun: true,
  navigate: () => {},
  copyText: () => {},
};

function inPage(children) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const rowMenu = (status) =>
  inPage(
    <RunRowMenu
      node={{ group: false }}
      data={run(status)}
      context={context}
      hostMenu={{ position: { top: 0, left: 0 } }}
      onHostClose={() => {}}
    />,
  );

// A disabled leaderboard menu item carries its reason in its tooltip, which
// labels (or titles) the item's wrapper.
const tipOf = (item) => {
  const wrapper = item.closest("[aria-label], [title]");
  return wrapper?.getAttribute("aria-label") ?? wrapper?.getAttribute("title");
};
const descriptionOf = (button) =>
  document.getElementById(button.getAttribute("aria-describedby"))?.textContent;
const lastToast = () => server.toasts[server.toasts.length - 1];
const wholeStops = () =>
  server.requests.filter((r) => r.url.endsWith("/stop/") && !r.body.run_ids);

beforeEach(() => {
  resetRunControlForTest();
  server.runControl = false;
  server.requests.length = 0;
  server.toasts.length = 0;
});

describe("a run's own Stop before the page knows run control is off", () => {
  it("asks once, says the reason instead of an error, and never stops the whole experiment", async () => {
    rowMenu("Running");
    const stop = screen.getByRole("menuitem", { name: "Stop" });
    expect(stop).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(stop);
    await waitFor(() => expect(server.toasts).toHaveLength(1));
    expect(lastToast().message).toBe(STOP_HELD);
    expect(lastToast().options.variant).toBe("info");
    expect(server.requests).toEqual([
      {
        method: "post",
        url: `/model-hub/experiments/v2/${EXP}/stop/`,
        body: { run_ids: [RUN] },
      },
    ]);
    expect(wholeStops()).toEqual([]);
    expect(runControlState()).toBe("off");

    // The open menu now holds the action with its reason; a click sends
    // nothing.
    const held = await screen.findByRole("menuitem", { name: "Stop" });
    await waitFor(() => expect(held).toHaveAttribute("aria-disabled", "true"));
    expect(tipOf(held)).toBe(STOP_HELD);
    fireEvent.click(held);
    expect(server.requests).toHaveLength(1);
  });

  it("a run's own Resume asks once, then says the shared line", async () => {
    rowMenu("Cancelled");
    fireEvent.click(screen.getByRole("menuitem", { name: "Resume · 6 left" }));
    await waitFor(() => expect(server.toasts).toHaveLength(1));
    expect(lastToast().message).toBe(NOT_IN_RELEASE);
    expect(lastToast().options.variant).toBe("info");
    expect(server.requests).toHaveLength(1);
    expect(server.requests[0].url).toBe(
      `/model-hub/experiments/v2/${EXP}/resume/`,
    );
    expect(wholeStops()).toEqual([]);
  });
});

describe("every door once the page knows run control is off", () => {
  beforeEach(() => noteRunControl("off"));

  it("leaderboard row menu: Stop and Resume are disabled with the reason", () => {
    rowMenu("Running");
    const stop = screen.getByRole("menuitem", { name: "Stop" });
    expect(stop).toHaveAttribute("aria-disabled", "true");
    expect(tipOf(stop)).toBe(STOP_HELD);
    fireEvent.click(stop);
    expect(server.requests).toEqual([]);
  });

  it("leaderboard row menu: Resume is disabled with the shared line", () => {
    rowMenu("Cancelled");
    const resume = screen.getByRole("menuitem", { name: "Resume · 6 left" });
    expect(resume).toHaveAttribute("aria-disabled", "true");
    expect(tipOf(resume)).toBe(NOT_IN_RELEASE);
    fireEvent.click(resume);
    expect(server.requests).toEqual([]);
  });

  it("leaderboard status chip: the hover Stop and Resume are disabled with the reason", () => {
    const { unmount } = inPage(
      <RunStatusCell
        node={{ group: false }}
        data={run("Running")}
        context={context}
      />,
    );
    const stop = screen.getByRole("button", { name: "Stop run" });
    expect(stop).toBeDisabled();
    expect(descriptionOf(stop)).toBe(STOP_HELD);
    fireEvent.click(stop);
    unmount();
    inPage(
      <RunStatusCell
        node={{ group: false }}
        data={run("Cancelled")}
        context={context}
      />,
    );
    const resume = screen.getByRole("button", { name: "Resume · 6 left" });
    expect(resume).toBeDisabled();
    expect(descriptionOf(resume)).toBe(NOT_IN_RELEASE);
    fireEvent.click(resume);
    expect(server.requests).toEqual([]);
  });

  it("results run chip: Stop and Resume are disabled with the reason under them", async () => {
    const bar = (status) => (
      <RunBar
        datasetId="ds"
        mode="experiment"
        runs={[run(status)]}
        colors={{}}
        onBaselineChange={() => {}}
      />
    );
    const { unmount } = inPage(bar("Running"));
    fireEvent.click(screen.getByLabelText("#1A menu"));
    const stop = await screen.findByRole("menuitem", { name: /^Stop #1A/ });
    expect(stop).toHaveAttribute("aria-disabled", "true");
    expect(stop).toHaveTextContent(STOP_HELD);
    fireEvent.click(stop);
    unmount();
    inPage(bar("Cancelled"));
    fireEvent.click(screen.getByLabelText("#1A menu"));
    const resume = await screen.findByRole("menuitem", {
      name: /^Resume #1A · 6 left/,
    });
    expect(resume).toHaveAttribute("aria-disabled", "true");
    expect(within(resume).getByText(NOT_IN_RELEASE)).toBeInTheDocument();
    fireEvent.click(resume);
    expect(server.requests).toEqual([]);
  });
});

describe("a run's own Stop where run control answers", () => {
  it("names the run, as before", async () => {
    server.runControl = true;
    rowMenu("Running");
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    await waitFor(() => expect(server.toasts).toHaveLength(1));
    expect(server.requests).toEqual([
      {
        method: "post",
        url: `/model-hub/experiments/v2/${EXP}/stop/`,
        body: { run_ids: [RUN] },
      },
    ]);
    expect(lastToast().message).toBe("Stopped #1A · 4 of 10 rows kept");
    expect(lastToast().options.variant).toBe("success");
    expect(typeof lastToast().options.undo?.onUndo).toBe("function");
    expect(wholeStops()).toEqual([]);
  });
});
