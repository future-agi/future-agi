/* eslint-disable react/prop-types */
// L3: the dataset Experiments tab body and header DevelopDetailView mounts.
// Prototype -> DSX leaderboard; non-prototype -> classic list and header,
// including a build with VITE_DSX_EXPERIMENTS_BASIC explicitly false.
// Both switch states are pinned the way the launch tests pin them: with
// unreleased parts hidden the cases above hold as written; as shipped
// (unreleased parts shown) a non-prototype build mounts the DSX leaderboard
// and header and, under the leaderboard, the experiments it cannot show in
// full (read from the experiments list's own rows; the group itself is
// rendered and tested in other-experiments-tab.test.jsx).
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  experimentsBasicFromEnv,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";

const unreleased = vi.hoisted(() => ({ shown: false }));
vi.mock("src/launch/launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});
const mode = vi.hoisted(() => ({ prototype: true }));
const mounts = vi.hoisted(() => ({
  dsxTab: 0,
  dsxHeader: 0,
  tab: 0,
  header: 0,
  refs: [],
}));
const requests = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => mode.prototype,
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: requests,
}));
vi.mock("../../ExperimentTab/ExperimentTab", async () => {
  const R = await import("react");
  return {
    default: R.forwardRef((props, ref) => {
      mounts.dsxTab += 1;
      mounts.refs.push(ref);
      return <div data-testid="dsx-leaderboard" />;
    }),
  };
});
vi.mock("../../ExperimentTab/ExperimentTabHeader", () => ({
  default: () => {
    mounts.dsxHeader += 1;
    return <div data-testid="dsx-header" />;
  },
}));
vi.mock("../ExperimentTab", async () => {
  const R = await import("react");
  return {
    default: R.forwardRef((props, ref) => {
      mounts.tab += 1;
      mounts.refs.push(ref);
      return <div data-testid="classic-list" />;
    }),
  };
});
vi.mock("../ExperimentTabHeader", () => ({
  default: () => {
    mounts.header += 1;
    return <div data-testid="classic-header" />;
  },
}));

import ExperimentTabSwitch from "../ExperimentTabSwitch";
import ExperimentTabHeaderSwitch from "../ExperimentTabHeaderSwitch";

const reset = () => {
  Object.assign(mounts, { dsxTab: 0, dsxHeader: 0, tab: 0, header: 0 });
  mounts.refs = [];
};

// Switch off: the override launchSwitch reads and the export the product
// modules read both say unreleased parts are hidden.
const switchOff = (state) =>
  setLaunchOverrideForTest({ ...state, showUnreleased: false });

let client;
const both = () => {
  const ref = React.createRef();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard/develop/d1?tab=experiments"]}>
        <Routes>
          <Route
            path="/dashboard/develop/:dataset"
            element={
              <>
                <ExperimentTabHeaderSwitch tabRef={ref} rowSelected={[]} />
                <ExperimentTabSwitch ref={ref} setRowSelected={() => {}} />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return ref;
};

describe("L3 Experiments tab switch", () => {
  beforeEach(() => {
    unreleased.shown = false;
    requests.get.mockReset();
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    client?.clear();
    reset();
  });

  it("prototype mode keeps the DSX leaderboard and header", () => {
    mode.prototype = true;
    switchOff({ launch: true, experimentsBasic: true });
    const ref = both();
    expect(screen.getByTestId("dsx-leaderboard")).toBeTruthy();
    expect(screen.getByTestId("dsx-header")).toBeTruthy();
    expect(mounts.tab + mounts.header).toBe(0);
    expect(mounts.refs).toContain(ref);
  });

  it("non-prototype + experimentsBasic mounts dev's list and header, never the leaderboard", () => {
    mode.prototype = false;
    switchOff({ launch: true, experimentsBasic: true });
    const ref = both();
    expect(screen.getByTestId("classic-list")).toBeTruthy();
    expect(screen.getByTestId("classic-header")).toBeTruthy();
    expect(mounts.dsxTab + mounts.dsxHeader).toBe(0);
    expect(mounts.refs).toContain(ref);
  });

  // Explicit OFF gates new surfaces, never the classic list.
  it("non-prototype with an explicit false build input mounts the classic list and header", () => {
    mode.prototype = false;
    const experimentsBasic = experimentsBasicFromEnv({
      VITE_DSX_EXPERIMENTS_BASIC: "false",
    });
    expect(experimentsBasic).toBe(false);
    switchOff({ launch: true, experimentsBasic });
    both();
    expect(screen.getByTestId("classic-list")).toBeVisible();
    expect(screen.getByTestId("classic-header")).toBeVisible();
    expect(mounts.dsxTab + mounts.dsxHeader).toBe(0);
    expect(requests.get).not.toHaveBeenCalled();
  });
});

describe("Experiments tab switch as shipped (unreleased parts shown)", () => {
  beforeEach(() => {
    unreleased.shown = true;
    mode.prototype = false;
    requests.get.mockReset();
    setLaunchOverrideForTest({ launch: true, experimentsBasic: true });
  });
  afterEach(() => {
    setLaunchOverrideForTest(null);
    client?.clear();
    unreleased.shown = false;
    reset();
  });

  it("mounts the DSX leaderboard and header, and reads the experiments list for the experiments it cannot show in full", async () => {
    requests.get.mockResolvedValue({
      data: {
        count: 2,
        next: null,
        results: [
          { id: "e1", name: "March prompts", opens_in_full_results: false },
          { id: "e2", name: "April prompts", opens_in_full_results: true },
        ],
      },
    });
    const ref = both();
    expect(screen.getByTestId("dsx-header")).toBeTruthy();
    expect(screen.getByTestId("dsx-leaderboard")).toBeTruthy();
    expect(mounts.tab + mounts.header).toBe(0);
    expect(mounts.refs).toContain(ref);
    await waitFor(() => expect(requests.get).toHaveBeenCalledTimes(1));
    const [url, { params }] = requests.get.mock.calls[0];
    expect(url).toBe("/model-hub/experiments/v2/list/");
    expect(params).toEqual({ dataset_id: "d1", page: 1, limit: 200 });
  });

  it("keeps prototype mode on the plain DSX leaderboard", () => {
    mode.prototype = true;
    both();
    expect(screen.getByTestId("dsx-leaderboard")).toBeTruthy();
    expect(requests.get).not.toHaveBeenCalled();
  });
});
